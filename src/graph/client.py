"""GraphClient — the Track A -> Track B interface.

FROZEN signatures (CLAUDE.md §12, §14.3). Track B codes against this from night
one; Track A fills in the bodies. Only the bodies change, never the signatures.

Layer 1 (search, get_docs) hits the SQLite FTS5 + vector index over all ~500K
documents. Layer 2 (everything else) hits HydraDB over Bolt. Which one a
question needs is the router's decision (Track B, B5), not this class's.

Layer 2 is implemented (A5). Layer 1 lands with A3.

Every Cypher string here is written against HydraDB's real subset. The rules and
the reasons are in src/graph/bolt.py; the two that bite hardest when editing
this file:

  * A relationship type is always a literal, never a parameter — so a query
    covering N relationship types is N queries, and rel_types=None means
    "every type in the ontology".
  * `e.id` must never appear in a RETURN. It fails as "unbound variable e",
    which looks like it is complaining about something else entirely.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from src.common.schemas import DocHit, Edge, Entity, NormalizedDoc, Path, PathStep
from src.graph.bolt import (
    FAR_FUTURE_TS,
    connect,
    is_valid_label,
    label_map,
    norm_name,
    run_read,
    surrogate_id,
    unpack_list,
    unpack_ts,
)

ALIAS_LABEL = "Alias"
HAS_ALIAS = "HAS_ALIAS"

# Projected on every edge read. `id` is deliberately absent — see module docstring.
_EDGE_PROPS = (
    "e.edge_id AS edge_id, e.src_canonical_id AS src_cid, "
    "e.dst_canonical_id AS dst_cid, e.stated_at_ts AS stated_at_ts, "
    "e.ingested_at_ts AS ingested_at_ts, e.valid_from_ts AS valid_from_ts, "
    "e.valid_to_ts AS valid_to_ts, e.is_current AS is_current, "
    "e.source_type AS source_type, e.source_doc_ids AS source_doc_ids, "
    "e.confidence AS confidence, e.contested AS contested, "
    "e.superseded_by AS superseded_by"
)


class NotBuiltYetError(NotImplementedError):
    """Raised by a stub whose Track A implementation has not landed.

    Track B: catching this and falling back to fixture data is a reasonable way
    to keep moving before the real implementation exists.
    """


class GraphClient:
    """Everything Track B needs to retrieve evidence.

    Track B never opens a Bolt session, a SQLite connection, or a Parquet file
    directly — it goes through here, so Track A can change storage without
    breaking the agent.
    """

    def __init__(self, uri: str | None = None, user: str | None = None,
                 password: str | None = None, index_dir: str | None = None) -> None:
        self.uri = uri
        self.user = user
        self.password = password
        self.index_dir = index_dir
        self._driver = None
        self._label_cache: dict[str, str] = {}

    # -- internals ----------------------------------------------------------

    @property
    def driver(self):
        if self._driver is None:
            self._driver = connect(self.uri, self.user, self.password)
        return self._driver

    def _read(self, cypher: str, **params) -> list[dict]:
        return run_read(self.driver, cypher, **params)

    def _node_labels(self, type: str | None = None) -> list[str]:
        """Node labels to search. A label is a literal in Cypher, so a query
        over "any entity" is one query per label."""
        mapping = label_map()
        if type:
            label = mapping.get(type, type if is_valid_label(type) else "")
            return [label] if label and is_valid_label(label) else []
        return [lab for lab in sorted(set(mapping.values()))
                if is_valid_label(lab) and lab != ALIAS_LABEL]

    def _label_of(self, cid: str) -> str:
        """Which label a canonical_id lives under.

        Needed because algo.MSpaths addresses nodes by (label, property, values)
        and will not accept a property without a label. Cached because a path
        query asks for the same handful of ids repeatedly.
        """
        if cid in self._label_cache:
            return self._label_cache[cid]
        for label in self._node_labels():
            rows = self._read(
                f"MATCH (n:{label}) WHERE n.canonical_id = $v RETURN n.name AS name",
                v=cid)
            if rows:
                self._label_cache[cid] = label
                return label
        self._label_cache[cid] = ""
        return ""

    def _group_by_label(self, cids: list[str]) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for cid in cids:
            label = self._label_of(cid)
            if label:
                grouped.setdefault(label, []).append(cid)
        return grouped

    def _edge_types(self, rel_types: Iterable[str] | None = None) -> list[str]:
        """Relationship types to query. A type is a literal in Cypher, so
        "all types" means one query each.

        HAS_ALIAS is excluded from the default set: it is structural bookkeeping
        that links an actor to its surface forms, not a fact about the world, so
        it would swamp neighbour and path results with noise.
        """
        if rel_types:
            return [r for r in rel_types if is_valid_label(r)]
        from src.common.config import ontology

        return [r for r in (ontology().get("edge_types") or {})
                if is_valid_label(r) and r != HAS_ALIAS]

    # -- Layer 1: full-corpus search ---------------------------------------

    def search(self, query: str, k: int = 20,
               sources: list[str] | None = None) -> list[DocHit]:
        """Hybrid keyword + vector search over all ~500K documents.

        Scores are reciprocal-rank-fused across FTS5 and the vector index.
        This is the workhorse for simple lookup questions and the main
        protection for the Document Recall metric.
        """
        raise NotBuiltYetError("A3")

    def get_docs(self, doc_ids: list[str]) -> list[NormalizedDoc]:
        """Fetch full normalized documents by id. Order follows doc_ids."""
        raise NotBuiltYetError("A3")

    # -- Layer 2: the ontology graph in HydraDB -----------------------------

    def find_entity(self, name_or_alias: str,
                    type: str | None = None) -> list[Entity]:
        """Resolve a surface form to canonical entities.

        Alias-aware by design: "Sam", "@soham" and "S. Ratnaparkhi" must all
        return the same entity. This is the entry point for nearly every
        graph question.

        Matching is case-insensitive via the precomputed name_lc property —
        HydraDB has no toLower(), so it could not be done at query time.
        """
        needle = norm_name(name_or_alias)
        if not needle:
            return []

        found: dict[str, str] = {}  # canonical_id -> entity_type

        # 1. an alias pointing at an actor — the "@soham" -> Sam case
        for row in self._read(
            f"MATCH (p)-[:{HAS_ALIAS}]->(a:{ALIAS_LABEL}) WHERE a.name_lc = $v "
            f"RETURN p.canonical_id AS cid, p.entity_type AS et", v=needle
        ):
            if row.get("cid"):
                found.setdefault(row["cid"], row.get("et") or "")

        # 2. a canonical name, per label
        for label in self._node_labels(type):
            for row in self._read(
                f"MATCH (n:{label}) WHERE n.name_lc = $v "
                f"RETURN n.canonical_id AS cid, n.entity_type AS et", v=needle
            ):
                if row.get("cid"):
                    found.setdefault(row["cid"], row.get("et") or "")

        if type:
            found = {cid: et for cid, et in found.items() if et == type}
        return [e for cid in found if (e := self.get_entity(cid)) is not None]

    def get_entity(self, cid: str) -> Entity | None:
        """One canonical entity with every alias attached.

        Not in the frozen §12 signature list, but Track B asked for id -> name
        resolution (progress/track-b.md, B5/B6 notes) and conflict answers read
        much better with it.
        """
        if not cid:
            return None
        node = None
        for label in self._node_labels():
            rows = self._read(
                f"MATCH (n:{label}) WHERE n.canonical_id = $v "
                f"RETURN n.canonical_id AS cid, n.name AS name, "
                f"n.entity_type AS et, n.mention_count AS mc, "
                f"n.source_types AS st", v=cid)
            if rows:
                node = rows[0]
                break
        if node is None:
            return None

        aliases: list[str] = []
        handles: list[str] = []
        emails: list[str] = []
        label = label_map().get(node.get("et") or "", "")
        if is_valid_label(label):
            for row in self._read(
                f"MATCH (p:{label})-[:{HAS_ALIAS}]->(a:{ALIAS_LABEL}) "
                f"WHERE p.canonical_id = $v RETURN a.name AS name, a.kind AS kind",
                v=cid
            ):
                bucket = {"handle": handles, "email": emails}.get(row.get("kind"), aliases)
                if row.get("name") and row["name"] not in bucket:
                    bucket.append(row["name"])

        return Entity(
            canonical_id=node["cid"],
            entity_type=node.get("et") or "",
            canonical_name=node.get("name") or node["cid"],
            aliases=aliases,
            handles=handles,
            emails=emails,
            mention_count=int(node.get("mc") or 0),
            source_types=unpack_list(node.get("st")),
        )

    def neighbors(self, cid: str, rel_types: list[str] | None = None,
                  at_time: datetime | None = None,
                  include_invalid: bool = False) -> list[Edge]:
        """Edges touching one entity.

        at_time -> "as of" query against the bi-temporal model: return edges
        whose validity window contains that instant. Default (None) means
        currently-valid edges only.

        include_invalid=True also returns superseded edges, which is what
        conflict answers need.
        """
        if not cid:
            return []
        edges: list[Edge] = []
        at_ts = int(at_time.timestamp()) if at_time else None

        for rel in self._edge_types(rel_types):
            for direction in ("out", "in"):
                pattern = (f"(s)-[e:{rel}]->(d)" if direction == "out"
                           else f"(s)-[e:{rel}]->(d)")
                anchor = "s" if direction == "out" else "d"
                where = [f"{anchor}.canonical_id = $v"]
                if at_ts is not None:
                    where.append("e.valid_from_ts <= $t AND e.valid_to_ts > $t")
                elif not include_invalid:
                    where.append("e.is_current = true")
                params = {"v": cid}
                if at_ts is not None:
                    params["t"] = at_ts
                try:
                    rows = self._read(
                        f"MATCH {pattern} WHERE {' AND '.join(where)} "
                        f"RETURN {_EDGE_PROPS}", **params)
                except Exception:
                    continue
                edges.extend(_row_to_edge(row, rel) for row in rows)

        return _dedupe_edges(edges)

    def paths(self, src_ids: list[str], dst_ids: list[str], max_len: int = 3,
              rel_types: list[str] | None = None) -> list[Path]:
        """Bounded multi-hop paths between entity sets.

        Wraps HydraDB's native algo.SPpaths / algo.SSpaths / algo.MSpaths and
        picks the right one from the shape of src_ids/dst_ids. This is the
        graph-native core of the submission — do not replace with hand-rolled
        BFS (CLAUDE.md §5, §11 A5).

        Which procedure applies:
          one source, one target   -> SPpaths  (integer node ids)
          many sources or targets  -> MSpaths  (addressed by canonical_id
                                                strings; a list parameter is
                                                rejected, so values are inlined)
          sources only, no target  -> SSpaths  (integer node id)
        """
        srcs = [s for s in (src_ids or []) if s]
        dsts = [d for d in (dst_ids or []) if d]
        if not srcs:
            return []

        rels = self._edge_types(rel_types)
        # HAS_ALIAS would let any two entities appear "connected" through a
        # surface form. It is structural, not semantic — keep it out of paths.
        rels = [r for r in rels if r != HAS_ALIAS]
        if not rels:
            return []
        rel_list = "[" + ", ".join(f"'{r}'" for r in rels) + "]"
        max_len = max(1, min(int(max_len), 3))

        if len(srcs) == 1 and len(dsts) == 1:
            return self._run_paths(
                f"CALL algo.SPpaths({{sourceNode: $s, targetNode: $t, "
                f"relTypes: {rel_list}, relDirection: 'both', maxLen: {max_len}, "
                f"pathCount: 10}}) YIELD path RETURN path",
                {"s": surrogate_id(srcs[0]), "t": surrogate_id(dsts[0])})

        if dsts:
            # MSpaths addresses nodes by (label, property, values) and rejects a
            # property without a label, so sources and targets are grouped by
            # label and each label pair is one call.
            out: list[Path] = []
            for src_label, src_group in self._group_by_label(srcs).items():
                for dst_label, dst_group in self._group_by_label(dsts).items():
                    src_list = _inline_strings(src_group)
                    dst_list = _inline_strings(dst_group)
                    if not src_list or not dst_list:
                        continue
                    out += self._run_paths(
                        f"CALL algo.MSpaths({{sourceLabel: '{src_label}', "
                        f"sourceProperty: 'canonical_id', sourceValues: {src_list}, "
                        f"targetLabel: '{dst_label}', targetProperty: 'canonical_id', "
                        f"targetValues: {dst_list}, relTypes: {rel_list}, "
                        f"relDirection: 'both', maxLen: {max_len}, pathCount: 10}}) "
                        f"YIELD path RETURN path", {})
            return out

        return self._run_paths(
            f"CALL algo.SSpaths({{sourceNode: $s, relTypes: {rel_list}, "
            f"relDirection: 'both', maxLen: {max_len}, pathCount: 25}}) "
            f"YIELD path RETURN path",
            {"s": surrogate_id(srcs[0])})

    def _run_paths(self, cypher: str, params: dict) -> list[Path]:
        try:
            rows = self._read(cypher, **params)
        except Exception:
            return []
        return [p for row in rows if (p := _to_path(row.get("path"))) is not None]

    def facts_about(self, cid: str, rel_type: str) -> list[Edge]:
        """Every assertion of one relation type about one entity, current and
        superseded, newest first.

        Conflict questions are answered from this: the current edge plus what it
        replaced, each with its source and date.
        """
        if not cid or not is_valid_label(rel_type):
            return []
        edges: list[Edge] = []
        for anchor in ("s", "d"):
            try:
                rows = self._read(
                    f"MATCH (s)-[e:{rel_type}]->(d) WHERE {anchor}.canonical_id = $v "
                    f"RETURN {_EDGE_PROPS}", v=cid)
            except Exception:
                continue
            edges.extend(_row_to_edge(row, rel_type) for row in rows)

        edges = _dedupe_edges(edges)
        # Current first, then most recently stated — the order a conflict answer
        # wants to read them out in.
        edges.sort(key=lambda e: (e.valid_to is not None,
                                  -(e.stated_at.timestamp() if e.stated_at else 0)))
        return edges

    def cypher(self, query: str, params: dict | None = None) -> list[dict]:
        """Escape hatch for anything the typed methods don't cover.

        Fine to use, but if Track B calls this repeatedly for the same shape of
        question, tell Track A and it becomes a real method.
        """
        return self._read(query, **(params or {}))

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Release the Bolt driver and any open index handles."""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def __enter__(self) -> GraphClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# row -> dataclass
# ---------------------------------------------------------------------------
def _inline_strings(values: list[str]) -> str:
    """Build a Cypher string list literal.

    Inlined rather than parameterised because HydraDB rejects a composite
    parameter outside UNWIND ("only supported as an UNWIND input"). Values that
    are not plain canonical ids are dropped rather than escaped — every id we
    mint is [A-Za-z0-9_.-], so anything else is a bug upstream, not a value to
    smuggle into a query.
    """
    safe = [v for v in values
            if v and all(ch.isalnum() or ch in "_.-" for ch in v)]
    return "[" + ", ".join(f"'{v}'" for v in safe) + "]" if safe else ""


def _row_to_edge(row: dict, rel_type: str) -> Edge:
    valid_to_raw = row.get("valid_to_ts")
    return Edge(
        edge_id=row.get("edge_id") or "",
        src_canonical_id=row.get("src_cid") or "",
        dst_canonical_id=row.get("dst_cid") or "",
        rel_type=rel_type,
        stated_at=unpack_ts(row.get("stated_at_ts")),
        ingested_at=unpack_ts(row.get("ingested_at_ts")) or datetime.now(),
        valid_from=unpack_ts(row.get("valid_from_ts")),
        # FAR_FUTURE is the "still current" sentinel; surface it as None so the
        # dataclass's is_current property and Track B's checks stay truthful.
        valid_to=None if _is_open(valid_to_raw) else unpack_ts(valid_to_raw),
        source_type=row.get("source_type") or "",
        source_doc_ids=unpack_list(row.get("source_doc_ids")),
        confidence=float(row.get("confidence") or 0.0),
        contested=bool(row.get("contested")),
        superseded_by=row.get("superseded_by") or None,
    )


def _is_open(value) -> bool:
    try:
        return int(value) >= FAR_FUTURE_TS
    except (TypeError, ValueError):
        return True


def _dedupe_edges(edges: list[Edge]) -> list[Edge]:
    """An edge matched from both ends appears twice; keep the first."""
    seen: dict[str, Edge] = {}
    for edge in edges:
        key = edge.edge_id or f"{edge.src_canonical_id}|{edge.rel_type}|{edge.dst_canonical_id}"
        seen.setdefault(key, edge)
    return list(seen.values())


def _node_to_entity(node) -> Entity:
    props = dict(node) if node is not None else {}
    return Entity(
        canonical_id=props.get("canonical_id") or "",
        entity_type=props.get("entity_type") or "",
        canonical_name=props.get("name") or props.get("canonical_id") or "",
        mention_count=int(props.get("mention_count") or 0),
        source_types=unpack_list(props.get("source_types")),
    )


def _to_path(raw) -> Path | None:
    """neo4j Path -> our Path. Returns None for anything unexpected rather than
    raising: one odd row must not take down a whole retrieval."""
    if raw is None or not hasattr(raw, "nodes"):
        return None
    nodes = list(raw.nodes)
    rels = list(raw.relationships)
    if not nodes:
        return None

    steps: list[PathStep] = []
    for i, rel in enumerate(rels):
        to_node = nodes[i + 1] if i + 1 < len(nodes) else nodes[-1]
        props = dict(rel)
        valid_to_raw = props.get("valid_to_ts")
        edge = Edge(
            edge_id=props.get("edge_id") or "",
            src_canonical_id=props.get("src_canonical_id") or "",
            dst_canonical_id=props.get("dst_canonical_id") or "",
            rel_type=rel.type,
            stated_at=unpack_ts(props.get("stated_at_ts")),
            ingested_at=unpack_ts(props.get("ingested_at_ts")) or datetime.now(),
            valid_from=unpack_ts(props.get("valid_from_ts")),
            valid_to=None if _is_open(valid_to_raw) else unpack_ts(valid_to_raw),
            source_type=props.get("source_type") or "",
            source_doc_ids=unpack_list(props.get("source_doc_ids")),
            confidence=float(props.get("confidence") or 0.0),
            contested=bool(props.get("contested")),
            superseded_by=props.get("superseded_by") or None,
        )
        steps.append(PathStep(edge=edge, to_entity=_node_to_entity(to_node)))

    return Path(start=_node_to_entity(nodes[0]), steps=steps)
