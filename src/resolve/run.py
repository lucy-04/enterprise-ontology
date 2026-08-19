"""Driver for B3: candidate mentions -> resolved canonical entities.

Two resolution paths, by entity type:
  - DETERMINISTIC types (ticket, pull_request, meeting, project, channel, team,
    role, component, product, topic, incident, organization, document): a
    mention's identity IS its key, so all mentions sharing a normalized key are
    one entity. No probabilities, no LLM. This is why cross-source ticket joins
    "just work".
  - PROBABILISTIC types (person, bot): Splink decides who is who
    (src/resolve/splink_er.py), because "Sam" vs "S. Ratnaparkhi" cannot be
    matched by string equality.

Writes, in the shapes frozen in schemas.py / CLAUDE.md §12:
    data/resolved/entities.parquet
    data/resolved/clusters.parquet
and, for the optional LLM adjudicator, data/resolved/middle_band.parquet.

CLI:
    python -m src.resolve.run --mentions data/candidates/mentions.parquet \
                              --out data/resolved
"""

from __future__ import annotations

import argparse
import hashlib
import re
from collections import Counter
from pathlib import Path

import pandas as pd

from src.common.schemas import Cluster, Entity
from src.resolve.base import normalize_name
from src.resolve.splink_er import resolve_people

# Types whose identity is their own key — merged by exact normalized surface form.
DETERMINISTIC_TYPES = {
    "ticket", "pull_request", "meeting", "project", "channel", "team", "role",
    "component", "product", "topic", "incident", "organization",
}
PROBABILISTIC_TYPES = {"person", "bot"}

_HANDLE_RE = re.compile(r"^[a-z][\w.\-]{1,30}$")


def _cid(entity_type: str, key: str) -> str:
    h = hashlib.sha1(f"{entity_type}\x1f{key}".encode()).hexdigest()[:16]
    return f"ent_{h}"


def _pick_canonical_name(surfaces: list[str]) -> str:
    """Prefer a full human-readable name over a handle or email.

    A proper name ("Karthik Iyer") beats a handle ("karthik") beats an email.
    Ties break on frequency then length.
    """
    counts = Counter(surfaces)

    def score(s: str) -> tuple:
        is_email = "@" in s
        is_proper = bool(re.match(r"^[A-Z]", s)) and not is_email
        has_space = " " in s
        return (is_proper, has_space, not is_email, counts[s], len(s))

    return max(surfaces, key=score)


def _entity_from_cluster(entity_type: str, group: pd.DataFrame,
                         canonical_id: str) -> Entity:
    surfaces = [str(s) for s in group["surface_form"].tolist()]
    distinct = list(dict.fromkeys(surfaces))
    emails = [s for s in distinct if "@" in s]
    handles = [s for s in distinct
               if "@" not in s and _HANDLE_RE.match(s) and " " not in s]
    return Entity(
        canonical_id=canonical_id,
        entity_type=entity_type,
        canonical_name=_pick_canonical_name(surfaces),
        aliases=distinct,
        handles=handles,
        emails=emails,
        mention_count=len(group),
        source_types=sorted(set(group["source_type"].tolist())),
    )


def resolve(mentions: pd.DataFrame) -> tuple[list[Entity], list[Cluster]]:
    entities: list[Entity] = []
    clusters: list[Cluster] = []

    # -- deterministic types --------------------------------------------------
    det = mentions[mentions["entity_type"].isin(DETERMINISTIC_TYPES)]
    for entity_type, type_group in det.groupby("entity_type"):
        key = type_group["surface_form"].astype(str).map(normalize_name)
        for norm_key, group in type_group.groupby(key):
            cid = _cid(entity_type, norm_key)
            entities.append(_entity_from_cluster(entity_type, group, cid))
            for mid in group["mention_id"]:
                clusters.append(Cluster(cid, mid, 1.0, "exact"))

    # -- documents: identity is doc_id (never merge by title) -----------------
    docs = mentions[mentions["entity_type"] == "document"]
    for doc_id, group in docs.groupby("doc_id"):
        cid = str(doc_id)   # canonical_id == doc_id, so A can link provenance directly
        entities.append(_entity_from_cluster("document", group, cid))
        for mid in group["mention_id"]:
            clusters.append(Cluster(cid, mid, 1.0, "exact"))

    # -- probabilistic types: people + bots via Splink ------------------------
    ppl = mentions[mentions["entity_type"].isin(PROBABILISTIC_TYPES)].copy()
    if not ppl.empty:
        cluster_of, cluster_rows, _middle = resolve_people(ppl)
        ppl["cluster"] = ppl["mention_id"].map(cluster_of)
        method_of = {r["mention_id"]: r["method"] for r in cluster_rows}
        for label, group in ppl.groupby("cluster"):
            # a cluster is people unless every member is a bot
            etype = "bot" if (group["entity_type"] == "bot").all() else "person"
            cid = _cid("person", f"cluster::{label}::{group['mention_id'].iloc[0]}")
            entities.append(_entity_from_cluster(etype, group, cid))
            for mid in group["mention_id"]:
                clusters.append(
                    Cluster(cid, mid, 1.0, method_of.get(mid, "splink"))
                )
    return entities, clusters


def _to_parquet(rows, columns, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        df = pd.DataFrame([{c: getattr(r, c) for c in columns} for r in rows])
    else:
        df = pd.DataFrame(columns=list(columns))
    df.to_parquet(path, index=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="B3 entity resolution")
    ap.add_argument("--mentions", default="data/candidates/mentions.parquet")
    ap.add_argument("--out", default="data/resolved")
    args = ap.parse_args(argv)

    mentions = pd.read_parquet(args.mentions)
    print(f"loaded {len(mentions)} mentions")

    entities, clusters = resolve(mentions)

    out = Path(args.out)
    _to_parquet(entities, Entity.PARQUET_COLUMNS, out / "entities.parquet")
    _to_parquet(clusters, Cluster.PARQUET_COLUMNS, out / "clusters.parquet")

    by_type = Counter(e.entity_type for e in entities)
    print(f"\nwrote {len(entities)} entities -> {out / 'entities.parquet'}")
    print("  by entity_type:", dict(by_type.most_common()))
    print(f"wrote {len(clusters)} cluster rows -> {out / 'clusters.parquet'}")
    merged = [e for e in entities if e.mention_count > 1]
    print(f"  {len(merged)} entities merged >1 mention "
          f"(top: {[(e.canonical_name, e.mention_count) for e in sorted(merged, key=lambda x: -x.mention_count)[:5]]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
