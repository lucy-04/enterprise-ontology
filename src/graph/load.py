"""A4 — load Track B's resolved graph into HydraDB.  `just load`

Reads the two Parquet files Track B produces (CLAUDE.md §12):

    data/resolved/entities.parquet   canonical entities
    data/graph/edges.parquet         bi-temporal edges, post-conflict-pass

and writes them into HydraDB over Bolt. Re-runnable from scratch by design —
Track B regenerates the graph repeatedly, so this drops and reloads rather than
trying to diff.

Two things this does beyond a straight copy, both forced by HydraDB's Cypher
subset (CLAUDE.md §5.1), both of which improve the data model rather than just
working around a limit:

  * **Alias nodes.** `aliases` / `handles` / `emails` arrive as Parquet lists,
    and HydraDB has no list properties. Instead of joining them into a string,
    each surface form becomes its own :Alias node linked by HAS_ALIAS. That is
    the more graph-native choice and it is literally the demo picture: one
    Person node with "Sam", "@soham" and "S. Ratnaparkhi" hanging off it.

  * **is_current.** `valid_to = null` cannot be queried (`IS NULL` is not
    supported), so validity is carried by an explicit boolean plus a
    far-future sentinel timestamp. Conflict queries filter on the boolean.

Usage:
    python -m src.graph.load                 # full drop + reload
    python -m src.graph.load --no-wipe       # add to what is already there
    python -m src.graph.load --limit 1000    # first N edges, for a smoke test
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from neo4j import Driver

from src.common.config import path
from src.graph.bolt import (
    FAR_FUTURE_TS,
    chunked,
    connect,
    is_valid_label,
    label_map,
    norm_name,
    pack_list,
    pack_str,
    pack_ts,
    run_read,
    run_write,
    surrogate_id,
)

# Entity types that get :Alias nodes. Aliasing a document or a ticket adds a
# node per document for no retrieval benefit; actors are where the surface-form
# problem actually lives, and where the demo looks.
ALIASED_TYPES = {"person", "bot", "organization", "team", "role"}

ALIAS_LABEL = "Alias"
HAS_ALIAS = "HAS_ALIAS"


# ---------------------------------------------------------------------------
# building rows
# ---------------------------------------------------------------------------
def _alias_canonical_id(owner_cid: str, kind: str, surface: str) -> str:
    """Alias ids are scoped to their owner on purpose.

    A shared :Alias node would let two different people who share a surface
    form ("ben") be joined by a 2-hop path through it, inventing a relationship
    that does not exist and quietly corrupting multi-hop answers. Scoping keeps
    every alias private to its entity.
    """
    import hashlib

    key = f"{owner_cid}\x00{kind}\x00{surface}".encode()
    return f"alias_{hashlib.blake2b(key, digest_size=8).hexdigest()}"


def build_node_rows(entities: pd.DataFrame) -> tuple[dict[str, list[dict]], list[dict]]:
    """entities.parquet -> per-label node rows, plus the alias rows to link.

    Returns ({label: [row, ...]}, [alias_link_row, ...]).
    """
    etype_to_label = label_map()
    by_label: dict[str, list[dict]] = defaultdict(list)
    alias_links: list[dict] = []
    unknown_types: set[str] = set()

    for row in entities.itertuples(index=False):
        cid = pack_str(getattr(row, "canonical_id", ""))
        if not cid:
            continue
        etype = pack_str(getattr(row, "entity_type", "")) or "unknown"
        label = etype_to_label.get(etype)
        if label is None or not is_valid_label(label):
            unknown_types.add(etype)
            continue

        name = pack_str(getattr(row, "canonical_name", "")) or cid
        by_label[label].append({
            "id": surrogate_id(cid),
            "canonical_id": cid,
            "name": name,
            "name_lc": norm_name(name),
            "entity_type": etype,
            "mention_count": int(getattr(row, "mention_count", 0) or 0),
            "source_types": pack_list(getattr(row, "source_types", None)),
        })

        if etype not in ALIASED_TYPES:
            continue

        # Every distinct surface form for this actor becomes one Alias node.
        seen: set[tuple[str, str]] = set()
        for kind, values in (("name", getattr(row, "aliases", None)),
                             ("handle", getattr(row, "handles", None)),
                             ("email", getattr(row, "emails", None))):
            for surface in _as_list(values):
                surface = pack_str(surface).strip()
                if not surface or (kind, surface) in seen:
                    continue
                seen.add((kind, surface))
                acid = _alias_canonical_id(cid, kind, surface)
                by_label[ALIAS_LABEL].append({
                    "id": surrogate_id(acid),
                    "canonical_id": acid,
                    "name": surface,
                    "name_lc": norm_name(surface),
                    "entity_type": "alias",
                    "mention_count": 0,
                    "source_types": "",
                    "kind": kind,
                })
                alias_links.append({
                    "src": surrogate_id(cid),
                    "dst": surrogate_id(acid),
                    "src_label": label,
                    "id": surrogate_id(f"{cid}->{acid}"),
                    "edge_id": f"edge_{acid}",
                    "src_canonical_id": cid,
                    "dst_canonical_id": acid,
                    "kind": kind,
                })

    if unknown_types:
        print(f"  warning: {len(unknown_types)} entity types absent from the "
              f"ontology, skipped: {sorted(unknown_types)}")
    return dict(by_label), alias_links


def _as_list(value: Any) -> list:
    """Parquet list columns arrive as ndarray/list/None. Never use truthiness:
    a numpy array raises on bool(), and a pandas NaN is a truthy float."""
    if value is None:
        return []
    if isinstance(value, float):  # NaN
        return []
    try:
        return list(value)
    except TypeError:
        return []


def build_edge_rows(edges: pd.DataFrame,
                    label_of: dict[str, str]) -> dict[tuple[str, str, str], list[dict]]:
    """edges.parquet -> rows grouped by (src_label, rel_type, dst_label).

    Grouped because a relationship batch needs exactly one literal label on each
    endpoint and one literal rel type; that is the unit HydraDB will accept.
    """
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    dropped = 0
    ingested_default = int(datetime.now(tz=UTC).timestamp())

    for row in edges.itertuples(index=False):
        src = pack_str(getattr(row, "src_canonical_id", ""))
        dst = pack_str(getattr(row, "dst_canonical_id", ""))
        rel = pack_str(getattr(row, "rel_type", ""))
        src_label, dst_label = label_of.get(src), label_of.get(dst)
        if not (src and dst and rel) or not src_label or not dst_label:
            dropped += 1
            continue
        if not (is_valid_label(src_label) and is_valid_label(dst_label)
                and is_valid_label(rel)):
            dropped += 1
            continue

        valid_to = pack_ts(getattr(row, "valid_to", None))
        is_current = valid_to == 0  # no valid_to recorded == still believed true
        edge_id = pack_str(getattr(row, "edge_id", "")) or f"edge_{src}_{rel}_{dst}"
        ingested = pack_ts(getattr(row, "ingested_at", None)) or ingested_default

        groups[(src_label, rel, dst_label)].append({
            "src": surrogate_id(src),
            "dst": surrogate_id(dst),
            # `id` is the relationship's reserved integer identity and is NOT
            # projectable in a RETURN; `edge_id` is the readable key.
            "id": surrogate_id(edge_id),
            "edge_id": edge_id,
            "src_canonical_id": src,
            "dst_canonical_id": dst,
            "stated_at_ts": pack_ts(getattr(row, "stated_at", None)),
            "ingested_at_ts": ingested,
            "valid_from_ts": pack_ts(getattr(row, "valid_from", None)),
            "valid_to_ts": valid_to if valid_to else FAR_FUTURE_TS,
            "is_current": bool(is_current),
            "source_type": pack_str(getattr(row, "source_type", "")),
            "source_doc_ids": pack_list(getattr(row, "source_doc_ids", None)),
            "confidence": float(getattr(row, "confidence", 1.0) or 0.0),
            "contested": bool(getattr(row, "contested", False)),
            "superseded_by": pack_str(getattr(row, "superseded_by", "")),
        })

    if dropped:
        print(f"  warning: dropped {dropped} edges whose endpoints are not in "
              f"entities.parquet (or whose type is not in the ontology)")
    return dict(groups)


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------
NODE_CYPHER = (
    "UNWIND $rows AS row MERGE (n {{id: row.id}}) "
    "SET n:{label}, n.canonical_id = row.canonical_id, n.name = row.name, "
    "n.name_lc = row.name_lc, n.entity_type = row.entity_type, "
    "n.mention_count = row.mention_count, n.source_types = row.source_types"
)
ALIAS_NODE_CYPHER = NODE_CYPHER + ", n.kind = row.kind"

EDGE_CYPHER = (
    "UNWIND $rows AS row "
    "MATCH (s:{src_label} {{id: row.src}}), (d:{dst_label} {{id: row.dst}}) "
    "CREATE (s)-[:{rel} {{id: row.id, edge_id: row.edge_id, "
    "src_canonical_id: row.src_canonical_id, dst_canonical_id: row.dst_canonical_id, "
    "stated_at_ts: row.stated_at_ts, ingested_at_ts: row.ingested_at_ts, "
    "valid_from_ts: row.valid_from_ts, valid_to_ts: row.valid_to_ts, "
    "is_current: row.is_current, source_type: row.source_type, "
    "source_doc_ids: row.source_doc_ids, confidence: row.confidence, "
    "contested: row.contested, superseded_by: row.superseded_by}}]->(d)"
)

ALIAS_EDGE_CYPHER = (
    "UNWIND $rows AS row "
    "MATCH (s:{src_label} {{id: row.src}}), (d:" + ALIAS_LABEL + " {{id: row.dst}}) "
    "CREATE (s)-[:" + HAS_ALIAS + " {{id: row.id, edge_id: row.edge_id, "
    "src_canonical_id: row.src_canonical_id, dst_canonical_id: row.dst_canonical_id, "
    "kind: row.kind}}]->(d)"
)


# Above this many nodes, clearing through Cypher is not worth waiting for.
WIPE_VIA_CYPHER_LIMIT = 2000
WIPE_BATCH = 50


def wipe(driver: Driver, labels: list[str]) -> None:
    """Drop every node we manage.

    Deletion is the slow direction in HydraDB. Measured on this build:
    DETACH DELETE runs at roughly 12 nodes/s, so a single
    `MATCH (n:Label) DETACH DELETE n` over a few hundred nodes already exceeds
    the 30s per-query timeout and dies part-way, leaving the graph half-wiped.
    Batching keeps each statement inside the timeout, but does nothing about the
    underlying rate: a 14K-node graph still takes ~19 minutes.

    So past a threshold this refuses, and points at `just db-reset`, which
    clears the store on disk in about a second. The loader is a drop-and-reload
    tool by design and the store is tens of MB, so that is the honest fast path
    rather than a workaround.

    Note the batch form takes no label — `UNWIND` batch node patterns reject
    them ("UNWIND batch node patterns do not support labels"), so nodes are
    matched by the id read back from the labelled query.
    """
    ids: list[int] = []
    for label in labels:
        if not is_valid_label(label):
            continue
        rows = run_read(driver, f"MATCH (n:{label}) RETURN n.id AS id")
        ids += [r["id"] for r in rows if r["id"] is not None]

    if not ids:
        return
    if len(ids) > WIPE_VIA_CYPHER_LIMIT:
        raise SystemExit(
            f"{len(ids):,} nodes to delete, and DETACH DELETE runs at ~12/s here "
            f"(~{len(ids) / 12 / 60:.0f} min).\n"
            f"Run `just db-reset`, restart with `just db-up`, then "
            f"`just load --no-wipe`.")

    for chunk in chunked(ids, WIPE_BATCH):
        run_write(driver, "UNWIND $rows AS row MATCH (n {id: row.id}) DETACH DELETE n",
                  [{"id": i} for i in chunk])


def load(entities: pd.DataFrame, edges: pd.DataFrame, driver: Driver,
         do_wipe: bool = True) -> dict[str, int]:
    label_of = {
        pack_str(r.canonical_id): label_map().get(pack_str(r.entity_type), "")
        for r in entities.itertuples(index=False)
    }

    print("building rows...")
    nodes_by_label, alias_links = build_node_rows(entities)
    edge_groups = build_edge_rows(edges, label_of)

    total_nodes = sum(len(v) for v in nodes_by_label.values())
    total_edges = sum(len(v) for v in edge_groups.values())
    print(f"  {total_nodes} nodes across {len(nodes_by_label)} labels")
    print(f"  {total_edges} edges across {len(edge_groups)} (src,rel,dst) groups")
    print(f"  {len(alias_links)} alias links")

    _check_collisions(nodes_by_label)

    all_labels = sorted(set(nodes_by_label) | {ALIAS_LABEL})
    if do_wipe:
        print("wiping existing graph...")
        wipe(driver, all_labels)

    t0 = time.time()
    print("writing nodes...")
    written_nodes = 0
    for label, rows in sorted(nodes_by_label.items()):
        cypher = (ALIAS_NODE_CYPHER if label == ALIAS_LABEL else NODE_CYPHER)
        written_nodes += run_write(driver, cypher.format(label=label), rows)
        print(f"    {label:<14} {len(rows):>7}")

    print("writing edges...")
    written_edges = 0
    for (src_label, rel, dst_label), rows in sorted(edge_groups.items()):
        cypher = EDGE_CYPHER.format(src_label=src_label, rel=rel, dst_label=dst_label)
        written_edges += run_write(driver, cypher, rows)

    if alias_links:
        print("writing alias links...")
        by_src_label: dict[str, list[dict]] = defaultdict(list)
        for link in alias_links:
            by_src_label[link["src_label"]].append(link)
        for src_label, rows in sorted(by_src_label.items()):
            written_edges += run_write(
                driver, ALIAS_EDGE_CYPHER.format(src_label=src_label), rows)

    dt = time.time() - t0
    print(f"\nwrote {written_nodes} nodes + {written_edges} edges in {dt:.1f}s")
    return {"nodes": written_nodes, "edges": written_edges,
            "labels": len(nodes_by_label), "seconds": int(dt)}


def _check_collisions(nodes_by_label: dict[str, list[dict]]) -> None:
    """Surrogate ids are hashes, so verify no two entities landed on one id.

    At ~1M nodes the birthday risk over 62 bits is ~1e-4 — small, but a silent
    collision would merge two unrelated entities into one node, which is exactly
    the failure this whole project is about not making.
    """
    seen: dict[int, str] = {}
    collisions = 0
    for rows in nodes_by_label.values():
        for row in rows:
            prior = seen.get(row["id"])
            if prior is not None and prior != row["canonical_id"]:
                collisions += 1
                if collisions <= 3:
                    print(f"  COLLISION: {prior!r} and {row['canonical_id']!r} "
                          f"share surrogate id {row['id']}")
            seen[row["id"]] = row["canonical_id"]
    if collisions:
        raise SystemExit(f"{collisions} surrogate-id collisions — refusing to load "
                         f"a graph that would silently merge unrelated entities")


def verify(driver: Driver, expected: dict[str, int]) -> bool:
    """Read the graph back. A write that reported success is not proof."""
    print("\nverifying...")
    ok = True
    labels = run_read(driver, "MATCH (n:Person) RETURN count(*) AS c")
    people = labels[0]["c"] if labels else 0
    print(f"  Person nodes: {people}")

    aliases = run_read(driver,
                       f"MATCH (p:Person)-[:{HAS_ALIAS}]->(a:{ALIAS_LABEL}) "
                       f"RETURN count(*) AS c")
    print(f"  Person->Alias links: {aliases[0]['c'] if aliases else 0}")

    if people == 0:
        print("  FAIL: no Person nodes in the graph")
        ok = False
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the resolved graph into HydraDB")
    parser.add_argument("--entities", default=str(path("resolved", "entities.parquet")))
    parser.add_argument("--edges", default=str(path("graph", "edges.parquet")))
    parser.add_argument("--no-wipe", action="store_true",
                        help="add to the existing graph instead of replacing it")
    parser.add_argument("--limit", type=int, default=0,
                        help="load only the first N edges (smoke test)")
    args = parser.parse_args(argv)

    try:
        entities = pd.read_parquet(args.entities)
        edges = pd.read_parquet(args.edges)
    except FileNotFoundError as exc:
        print(f"missing input: {exc}")
        print("Run Track B's pipeline first: just extract && just resolve && just conflicts")
        return 1

    if args.limit:
        edges = edges.head(args.limit)

    print(f"loaded {len(entities)} entities, {len(edges)} edges from disk")

    driver = connect()
    try:
        stats = load(entities, edges, driver, do_wipe=not args.no_wipe)
        ok = verify(driver, stats)
    finally:
        driver.close()

    print("\nOK" if ok else "\nFAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
