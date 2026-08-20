"""A local GraphClient over Track B's own Parquet outputs — TEST INFRA ONLY.

Track A owns the real GraphClient (HydraDB + the search index). Until that lands,
this in-memory double implements the same interface over the files B produces
(entities.parquet, edges.parquet) plus a trivial keyword search over the
normalized fixture. It lets us exercise the router end-to-end and self-score
before A's server exists. It is NOT the production client and never ships.
"""

from __future__ import annotations

import re
from datetime import datetime

import pandas as pd

from src.common.schemas import DocHit, Edge, Entity, NormalizedDoc
from src.graph.client import GraphClient


def _dt(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v):
        return None
    return v.to_pydatetime() if hasattr(v, "to_pydatetime") else v


def _lst(v) -> list:
    """None/NaN/ndarray -> a plain list (ndarray truthiness is ambiguous)."""
    if v is None:
        return []
    return list(v)


class LocalGraphClient(GraphClient):
    def __init__(self, normalized: str, entities: str, edges: str) -> None:
        super().__init__()
        self.docs = pd.read_parquet(normalized)
        self.ents = pd.read_parquet(entities)
        self.edges_df = pd.read_parquet(edges)
        self._doc_by_id = {r.doc_id: r for r in self.docs.itertuples(index=False)}

    # -- Layer 1 --------------------------------------------------------------
    def search(self, query: str, k: int = 20, sources=None) -> list[DocHit]:
        terms = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2]
        if not terms:
            return []
        hits = []
        for r in self.docs.itertuples(index=False):
            if sources and r.source_type not in sources:
                continue
            text = f"{r.title}\n{r.body}".lower()
            score = sum(text.count(t) for t in terms)
            if score:
                snippet = (r.body or "")[:200].replace("\n", " ").strip()
                hits.append(DocHit(doc_id=r.doc_id, source_type=r.source_type,
                                   title=r.title or "", snippet=snippet,
                                   score=float(score), timestamp=_dt(r.timestamp)))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    def get_docs(self, doc_ids: list[str]) -> list[NormalizedDoc]:
        out = []
        for did in doc_ids:
            r = self._doc_by_id.get(did)
            if r is not None:
                out.append(NormalizedDoc(
                    doc_id=r.doc_id, source_type=r.source_type, title=r.title or "",
                    body=r.body or "", timestamp=_dt(r.timestamp),
                    author_refs=_lst(r.author_refs),
                    mention_refs=_lst(r.mention_refs),
                    thread_id=r.thread_id, path=r.path or "", raw_metadata={}))
        return out

    # -- Layer 2 --------------------------------------------------------------
    def _entity_row(self, cid: str):
        m = self.ents[self.ents.canonical_id == cid]
        return m.iloc[0] if len(m) else None

    def _to_entity(self, row) -> Entity:
        return Entity(
            canonical_id=row.canonical_id, entity_type=row.entity_type,
            canonical_name=row.canonical_name, aliases=_lst(row.aliases),
            handles=_lst(row.handles), emails=_lst(row.emails),
            mention_count=int(row.mention_count), source_types=_lst(row.source_types))

    def get_entity(self, cid: str) -> Entity | None:
        """Resolve a canonical id to its Entity (mirrors the real GraphClient.get_entity,
        added in A5). Lets the router's naming() fallback turn far-end edge ids into
        real names in local checks, not just against HydraDB."""
        row = self._entity_row(cid)
        return self._to_entity(row) if row is not None else None

    def find_entity(self, name_or_alias: str, type: str | None = None) -> list[Entity]:
        q = name_or_alias.strip().lower()
        out = []
        for row in self.ents.itertuples(index=False):
            if type and row.entity_type != type:
                continue
            names = [str(row.canonical_name).lower()] + [str(a).lower() for a in _lst(row.aliases)]
            if any(q == n or (len(q) > 2 and q in n) for n in names):
                out.append(self._to_entity(row))
        return out

    def _to_edge(self, row) -> Edge:
        return Edge(
            edge_id=row.edge_id, src_canonical_id=row.src_canonical_id,
            dst_canonical_id=row.dst_canonical_id, rel_type=row.rel_type,
            stated_at=_dt(row.stated_at), ingested_at=_dt(row.ingested_at),
            valid_from=_dt(row.valid_from), valid_to=_dt(row.valid_to),
            source_type=row.source_type, source_doc_ids=_lst(row.source_doc_ids),
            confidence=float(row.confidence), contested=bool(row.contested),
            superseded_by=(None if pd.isna(row.superseded_by) else row.superseded_by))

    def neighbors(self, cid, rel_types=None, at_time=None, include_invalid=False) -> list[Edge]:
        df = self.edges_df[(self.edges_df.src_canonical_id == cid) |
                           (self.edges_df.dst_canonical_id == cid)]
        out = []
        for row in df.itertuples(index=False):
            if rel_types and row.rel_type not in rel_types:
                continue
            edge = self._to_edge(row)
            if not include_invalid and edge.valid_to is not None:
                continue
            out.append(edge)
        return out

    def facts_about(self, cid: str, rel_type: str) -> list[Edge]:
        df = self.edges_df[(self.edges_df.src_canonical_id == cid) &
                           (self.edges_df.rel_type == rel_type)]
        edges = [self._to_edge(r) for r in df.itertuples(index=False)]
        edges.sort(key=lambda e: e.stated_at or datetime.min, reverse=True)
        return edges

    def paths(self, src_ids, dst_ids, max_len=3, rel_types=None):
        from src.common.schemas import Path, PathStep
        # simple BFS over current edges for the test double
        adj: dict[str, list[Edge]] = {}
        for row in self.edges_df.itertuples(index=False):
            if pd.notna(row.valid_to):
                continue
            adj.setdefault(row.src_canonical_id, []).append(self._to_edge(row))
        results = []
        dst_set = set(dst_ids)
        for s in src_ids:
            start_row = self._entity_row(s)
            if start_row is None:
                continue
            start = self._to_entity(start_row)
            queue = [(s, [])]
            while queue:
                node, steps = queue.pop(0)
                if len(steps) > max_len:
                    continue
                if node in dst_set and steps:
                    results.append(Path(start=start, steps=steps))
                    continue
                for edge in adj.get(node, []):
                    nxt = edge.dst_canonical_id
                    nrow = self._entity_row(nxt)
                    if nrow is None or any(st.edge.edge_id == edge.edge_id for st in steps):
                        continue
                    queue.append((nxt, steps + [PathStep(edge=edge, to_entity=self._to_entity(nrow))]))
        return results[:10]

    def cypher(self, query, params=None):
        return []
