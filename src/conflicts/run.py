"""B4 — conflict resolution + the bi-temporal edge model.

Turns per-document relation candidates into the final graph edges that Track A
loads into HydraDB (data/graph/edges.parquet). Two jobs:

1. LIFT relations from mentions to canonical entities. A relation candidate links
   two *mentions*; here we map each mention to its resolved canonical_id (from
   B3's clusters) and collapse duplicate assertions of the same
   (src, rel_type, dst) from many documents into ONE edge that remembers all its
   source documents.

2. RESOLVE CONFLICTS bi-temporally (the Graphiti pattern, CLAUDE.md §4/§11 B4).
   For "single-valued" relations — a person has one current team, a ticket one
   current owner — two edges with the same (src, rel_type) but different targets
   are a contradiction. We NEVER delete the loser: we set its valid_to and point
   superseded_by at the winner, so the system can still answer "Priya owns it
   now, Sam did until March". The winner is chosen by a source-priority table
   (systems-of-record beat chat) with recency as the tie-break. Genuine ties
   (equal priority, equal/unknown dates) are marked contested=True and BOTH stay
   current, so the answer shows both sides instead of silently picking one.

HYDRADB NOTE (§5.1): we express "currently true" with valid_to = None (the
contract's convention). HydraDB cannot query for null, so Track A's loader
translates valid_to=None into an explicit is_current=true boolean at load time.
No LLM here.
"""

from __future__ import annotations

import argparse
import hashlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

from src.common.schemas import Edge

_ONTOLOGY = Path(__file__).resolve().parents[2] / "ontology" / "ontology.yaml"

# Relations where one side has ONE current counterpart, so a second one is a
# contradiction/change rather than an added fact. Direction matters:
#   SRC_SINGLE: each SOURCE has one current target — group by src.
#               "a person belongs to one current team / works for one employer".
#   DST_SINGLE: each TARGET has one current source — group by dst.
#               "a ticket/page has one current owner", regardless of how many
#               things that owner owns.
# Everything else (POSTED_IN, MENTIONS, REFERENCES, PART_OF, SENT, ...) is
# multi-valued: no conflict, every edge stays current.
SRC_SINGLE = {
    "MEMBER_OF",     # person -> one current team (the "bob (eng-runtime)" vs "(sre)" case)
    "WORKS_FOR",     # person -> one current employer
    "HAS_ROLE",      # person -> one current role in a context
    "REPORTS_TO",    # person -> one current manager
    "ASSIGNED_TO",   # ticket -> one current assignee
}
DST_SINGLE = {
    "OWNS",          # a thing has one current owner (owner may own many things)
}
SINGLE_VALUED = SRC_SINGLE | DST_SINGLE

_MIN_TS = datetime.min


def load_source_priority() -> dict[str, int]:
    with _ONTOLOGY.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh).get("source_priority", {})


def _edge_id(src: str, rel: str, dst: str) -> str:
    h = hashlib.sha1(f"{src}\x1f{rel}\x1f{dst}".encode()).hexdigest()[:16]
    return f"edge_{h}"


def lift_to_entities(relations: pd.DataFrame, clusters: pd.DataFrame,
                     now: datetime) -> dict[tuple[str, str, str], Edge]:
    """Map relation candidates onto canonical entities and dedupe into edges."""
    cid_of = dict(zip(clusters["mention_id"], clusters["canonical_id"]))
    edges: dict[tuple[str, str, str], Edge] = {}

    for row in relations.itertuples(index=False):
        src = cid_of.get(row.src_mention_id)
        dst = cid_of.get(row.dst_mention_id)
        if not src or not dst or src == dst:
            continue  # unresolved endpoint or a self-loop
        key = (src, row.rel_type, dst)
        stated = None if pd.isna(row.stated_at) else row.stated_at.to_pydatetime() \
            if hasattr(row.stated_at, "to_pydatetime") else row.stated_at
        conf = float(row.confidence)

        edge = edges.get(key)
        if edge is None:
            edges[key] = Edge(
                edge_id=_edge_id(*key),
                src_canonical_id=src, dst_canonical_id=dst, rel_type=row.rel_type,
                stated_at=stated, ingested_at=now, valid_from=stated, valid_to=None,
                source_type=row.source_type,
                source_doc_ids=[row.doc_id], confidence=conf,
                contested=False, superseded_by=None,
            )
        else:
            # merge another document's assertion of the same fact
            if row.doc_id not in edge.source_doc_ids:
                edge.source_doc_ids.append(row.doc_id)
            edge.confidence = max(edge.confidence, conf)
            # keep the earliest stated_at as valid_from; remember the latest too
            if stated and (edge.stated_at is None or stated < edge.stated_at):
                edge.stated_at = edge.valid_from = stated
    return edges


def resolve_conflicts(edges: dict[tuple[str, str, str], Edge],
                      priority: dict[str, int]) -> list[Edge]:
    """Apply the bi-temporal conflict pass to single-valued relations in place."""
    def rank_key(e: Edge) -> tuple[int, datetime]:
        # higher priority first, then more recent
        return (priority.get(e.source_type, 0), e.stated_at or _MIN_TS)

    # group on whichever side is single-valued; a conflict is >1 distinct
    # counterpart within a group. src-single groups by src (compare targets),
    # dst-single groups by dst (compare sources).
    groups: dict[tuple[str, str], list[Edge]] = defaultdict(list)
    for (src, rel, dst), edge in edges.items():
        if rel in SRC_SINGLE:
            groups[(src, rel)].append(edge)
        elif rel in DST_SINGLE:
            groups[(dst, rel)].append(edge)

    conflicts = 0
    for (_anchor, rel), group in groups.items():
        counterparts = ({e.dst_canonical_id for e in group} if rel in SRC_SINGLE
                        else {e.src_canonical_id for e in group})
        if len(counterparts) < 2:
            continue  # no disagreement

        ranked = sorted(group, key=rank_key, reverse=True)
        top_key = rank_key(ranked[0])
        winners = [e for e in ranked if rank_key(e) == top_key]
        losers = [e for e in ranked if rank_key(e) < top_key]

        # If EVERYTHING ties (same source, same/unknown date) we cannot justify
        # calling one a supersession of another — these are coexisting facts (a
        # person genuinely in two teams), not a contradiction. Leave them alone.
        if not losers:
            continue

        if len(winners) > 1:
            # a real winner exists over the losers, but the top itself is tied:
            # mark the tied top contested (show both) and still supersede the losers.
            for e in winners:
                e.contested = True          # stays current (valid_to = None)

        winner = ranked[0]
        cutoff = winner.stated_at or winner.ingested_at
        for e in losers:
            e.valid_to = cutoff             # superseded as of when the winner took over
            e.superseded_by = winner.edge_id
        conflicts += 1

    print(f"  resolved {conflicts} conflict groups "
          f"(single-valued relations with disagreeing targets)")
    return list(edges.values())


def build_edges(relations: pd.DataFrame, clusters: pd.DataFrame) -> list[Edge]:
    now = datetime.now()
    edges = lift_to_entities(relations, clusters, now)
    return resolve_conflicts(edges, load_source_priority())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="B4 conflict + bi-temporal edges")
    ap.add_argument("--relations", default="data/candidates/relations.parquet")
    ap.add_argument("--clusters", default="data/resolved/clusters.parquet")
    ap.add_argument("--out", default="data/graph")
    args = ap.parse_args(argv)

    relations = pd.read_parquet(args.relations)
    clusters = pd.read_parquet(args.clusters)
    print(f"loaded {len(relations)} relation candidates, {len(clusters)} cluster rows")

    edges = build_edges(relations, clusters)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([{c: getattr(e, c) for c in Edge.PARQUET_COLUMNS} for e in edges])
    df.to_parquet(out / "edges.parquet", index=False)

    current = sum(1 for e in edges if e.valid_to is None)
    superseded = sum(1 for e in edges if e.valid_to is not None)
    contested = sum(1 for e in edges if e.contested)
    print(f"\nwrote {len(edges)} edges -> {out / 'edges.parquet'}")
    print(f"  current: {current} | superseded: {superseded} | contested: {contested}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
