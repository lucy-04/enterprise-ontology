"""Low-level HydraDB access over Bolt, shared by the loader (A4) and client (A5).

Everything in here exists because HydraDB's OpenCypher subset is narrow in ways
that are NOT obvious and NOT documented in one place. Each rule below was
measured against a running node on 2026-08-19; the full list lives in
CLAUDE.md §5.1. Read them before changing a query — they are parse-time
rejections, not style preferences.

The five that shape every line of this module:

1. Batched writes (`UNWIND $rows`) only work over **Bolt**. The HTTP /query
   endpoint routes to the in-process shard API, which takes scalar parameters
   only and rejects every UNWIND form with a misleading "not executable" error.

2. A vertex upsert must be `MERGE (n {id: row.x}) SET n:Label, n.p = row.p`,
   with **exactly one** SET label. Two labels are rejected, so a node cannot
   carry both a generic `:Entity` marker and its real type — we use the real
   type and keep `entity_type` as a property.

3. A relationship batch must be `UNWIND $rows AS row MATCH (s:L1 {id: row.s}),
   (d:L2 {id: row.d}) CREATE (s)-[:REL {id: row.i, ...}]->(d)`. Both endpoints
   need exactly one label each, the rel type is a literal, and **every** edge
   property must read from the row map — a literal like `{is_current: true}`
   is rejected. Hence rows are grouped by (src_label, rel_type, dst_label).

4. **Never project `e.id`.** `id` is the relationship's reserved identity;
   selecting it fails with "unbound variable e", which reads like a syntax
   error somewhere else entirely and sends you hunting in the wrong place.
   The retrievable string key is stored separately as `edge_id`.

5. Batches are capped by admission control at 1024 items per statement
   ("client_query_batch_items ... exceeds limit 1024").

Property values may only be int / float / bool / string — no lists, no nulls,
no temporals. `pack_*` / `unpack_*` below are the single place that encoding
lives, so the loader and the client can never disagree about it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Any

from neo4j import Driver, GraphDatabase

from src.common.config import hydra_config

# Admission control rejects >1024 items in one batch. Stay under it.
BATCH_SIZE = 500

# Node ids must be non-negative integers. 62 bits round-trips intact (verified);
# ~1e-4 collision risk at 1M nodes, and the loader checks for collisions anyway.
ID_BITS = 62
ID_MASK = (1 << ID_BITS) - 1

# There are no null property values, so "no timestamp" and "still true" need
# sentinels. 0 = unknown/absent. FAR_FUTURE = open-ended, i.e. currently valid.
NULL_TS = 0
FAR_FUTURE_TS = 4102444800  # 2100-01-01T00:00:00Z

# Lists are not storable. Doc ids and source types are joined with this; neither
# ever contains it, so the split is lossless.
SEP = "|"


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------
def surrogate_id(canonical_id: str) -> int:
    """Map a string canonical_id to the non-negative integer HydraDB requires.

    Derived by hash rather than a counter so it is stable across reloads without
    persisting a mapping table — a reload must land the same node on the same id
    or every previously-returned id becomes wrong.
    """
    digest = hashlib.blake2b(canonical_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") & ID_MASK


# ---------------------------------------------------------------------------
# property encoding — the only place this is decided
# ---------------------------------------------------------------------------
def pack_ts(value: Any) -> int:
    """datetime | None -> epoch seconds, 0 when absent."""
    if value is None:
        return NULL_TS
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return NULL_TS
    try:
        if value != value:  # NaT / NaN — never trust truthiness of a pandas cell
            return NULL_TS
    except TypeError:
        pass
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        return NULL_TS
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp())


def unpack_ts(value: Any) -> datetime | None:
    """Epoch seconds -> datetime, with both sentinels read back as None."""
    if value is None:
        return None
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    if ts in (NULL_TS, FAR_FUTURE_TS):
        return None
    return datetime.fromtimestamp(ts, tz=UTC).replace(tzinfo=None)


def pack_list(values: Any) -> str:
    """list[str] -> delimited string, because list properties are rejected."""
    if values is None:
        return ""
    try:
        if values != values:  # NaN
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(values, str):
        return values
    out = [str(v) for v in values if v is not None and str(v)]
    return SEP.join(out)


def unpack_list(value: Any) -> list[str]:
    if not value:
        return []
    return [part for part in str(value).split(SEP) if part]


def pack_str(value: Any) -> str:
    """None / NaN -> "" so a property is never null."""
    if value is None:
        return ""
    try:
        if value != value:
            return ""
    except (TypeError, ValueError):
        pass
    return str(value)


def norm_name(value: Any) -> str:
    """Lowercased form stored alongside every name.

    HydraDB has no toLower(), so case-insensitive lookup has to be precomputed
    at load time or it is not possible at query time at all.
    """
    return pack_str(value).strip().lower()


# ---------------------------------------------------------------------------
# connection
# ---------------------------------------------------------------------------
def connect(uri: str | None = None, user: str | None = None,
            password: str | None = None) -> Driver:
    """Bolt driver against the local node. Auth is the dev token as password."""
    cfg = hydra_config()
    return GraphDatabase.driver(
        uri or cfg["uri"],
        auth=("neo4j", password or cfg["password"] or "local-development-token-32-bytes"),
    )


def chunked(rows: Sequence[dict], size: int = BATCH_SIZE) -> Iterator[list[dict]]:
    for i in range(0, len(rows), size):
        yield list(rows[i:i + size])


def run_write(driver: Driver, cypher: str, rows: Sequence[dict],
              size: int = BATCH_SIZE) -> int:
    """Execute one UNWIND statement over as many batches as the rows need."""
    written = 0
    for batch in chunked(rows, size):
        with driver.session() as session:
            session.run(cypher, rows=batch).consume()
        written += len(batch)
    return written


def run_read(driver: Driver, cypher: str, **params: Any) -> list[dict]:
    with driver.session() as session:
        return [dict(record) for record in session.run(cypher, **params)]


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------
def label_map() -> dict[str, str]:
    """entity_type (Track B's string) -> node label, read from the ontology.

    Track B owns ontology.yaml, so the mapping is derived from it rather than
    duplicated here; a new node type appears in the graph without touching this.
    """
    from src.common.config import ontology

    mapping: dict[str, str] = {}
    for label, spec in (ontology().get("node_types") or {}).items():
        etype = (spec or {}).get("entity_type")
        if etype:
            mapping[str(etype)] = str(label)
    return mapping


def is_valid_label(label: str) -> bool:
    """Labels are interpolated into Cypher literally, so they must be safe."""
    return bool(label) and label.replace("_", "").isalnum() and not label[0].isdigit()
