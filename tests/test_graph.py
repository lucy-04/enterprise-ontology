"""A4/A5 regression tests — the loader and the HydraDB-backed GraphClient.

Two groups:

* Encoding and row-building tests run everywhere with no database. They pin the
  property-encoding decisions forced by HydraDB's type system (no lists, no
  nulls, no temporals), because a silent change there corrupts every edge.

* Integration tests need a live node and skip cleanly without one, so the suite
  stays green on a machine that is not running HydraDB. They are the ones that
  actually prove the Cypher is accepted — a unit test cannot, since every
  constraint here is enforced at parse time by the server.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from src.graph import load as loader
from src.graph.bolt import (
    FAR_FUTURE_TS,
    is_valid_label,
    norm_name,
    pack_list,
    pack_str,
    pack_ts,
    surrogate_id,
    unpack_list,
    unpack_ts,
)

# ---------------------------------------------------------------------------
# encoding
# ---------------------------------------------------------------------------


def test_surrogate_ids_are_stable_and_non_negative():
    """Node ids must be non-negative integers, and a reload has to reproduce
    them exactly or every id handed out earlier becomes wrong."""
    first = surrogate_id("ent_abc123")
    assert first == surrogate_id("ent_abc123")
    assert first >= 0
    assert surrogate_id("ent_abc123") != surrogate_id("ent_abc124")


def test_surrogate_ids_fit_in_the_range_hydradb_round_trips():
    for cid in ("ent_0", "dsid_" + "f" * 32, "ENG-30521", "alias_deadbeef"):
        assert 0 <= surrogate_id(cid) < (1 << 62)


def test_timestamps_round_trip_through_epoch_ints():
    when = datetime(2026, 3, 15, 10, 2, 0, tzinfo=UTC)
    assert unpack_ts(pack_ts(when)) == when.replace(tzinfo=None)


def test_absent_timestamps_become_zero_not_null():
    """There are no null property values, so absence needs a sentinel."""
    assert pack_ts(None) == 0
    assert pack_ts(pd.NaT) == 0
    assert unpack_ts(0) is None


def test_far_future_sentinel_reads_back_as_still_current():
    """valid_to has no null, so "open ended" is a far-future timestamp; it must
    surface as None or Edge.is_current lies."""
    assert unpack_ts(FAR_FUTURE_TS) is None


def test_lists_survive_the_join_split_round_trip():
    docs = ["dsid_aaa", "dsid_bbb", "dsid_ccc"]
    assert unpack_list(pack_list(docs)) == docs
    assert unpack_list(pack_list([])) == []
    assert unpack_list(pack_list(None)) == []


def test_pandas_nan_never_leaks_into_a_property():
    """pandas coerces missing values to NaN, which is a truthy float — the bug
    that has already bitten this project twice (progress/track-b.md, B3/B5)."""
    assert pack_str(float("nan")) == ""
    assert pack_list(float("nan")) == ""
    assert pack_ts(float("nan")) == 0


def test_label_validation_rejects_injection():
    """Labels and relationship types are interpolated into Cypher literally."""
    assert is_valid_label("Person")
    assert is_valid_label("MEMBER_OF")
    assert not is_valid_label("Person) DETACH DELETE (n")
    assert not is_valid_label("")
    assert not is_valid_label("9Lives")


def test_norm_name_is_case_and_space_insensitive():
    """HydraDB has no toLower(), so case-insensitive lookup is precomputed."""
    assert norm_name("  Sam Ratnaparkhi ") == norm_name("SAM RATNAPARKHI")


# ---------------------------------------------------------------------------
# row building
# ---------------------------------------------------------------------------
def _entities_frame() -> pd.DataFrame:
    return pd.DataFrame([
        {"canonical_id": "ent_sam", "entity_type": "person",
         "canonical_name": "Sam Ratnaparkhi", "aliases": ["Sam", "S. Ratnaparkhi"],
         "handles": ["soham"], "emails": ["sam@redwood.com"],
         "mention_count": 9, "source_types": ["slack", "gmail"]},
        {"canonical_id": "ent_team", "entity_type": "team",
         "canonical_name": "Support", "aliases": ["Support"], "handles": [],
         "emails": [], "mention_count": 3, "source_types": ["slack"]},
        {"canonical_id": "dsid_abc", "entity_type": "document",
         "canonical_name": "a doc", "aliases": [], "handles": [], "emails": [],
         "mention_count": 1, "source_types": ["slack"]},
    ])


def test_actor_surface_forms_become_alias_nodes():
    """aliases/handles/emails are Parquet lists and HydraDB has no list
    properties, so each surface form becomes its own node."""
    by_label, links = loader.build_node_rows(_entities_frame())
    alias_names = {row["name"] for row in by_label["Alias"]}
    assert {"Sam", "S. Ratnaparkhi", "soham", "sam@redwood.com"} <= alias_names
    assert all(link["src"] == surrogate_id("ent_sam")
               for link in links if link["kind"] == "email")


def test_alias_nodes_are_scoped_to_their_owner():
    """A shared alias node would let two people who happen to share a surface
    form be joined by a 2-hop path, inventing a relationship."""
    frame = pd.DataFrame([
        {"canonical_id": "ent_ben1", "entity_type": "person", "canonical_name": "Ben Carter",
         "aliases": ["ben"], "handles": [], "emails": [], "mention_count": 1,
         "source_types": ["slack"]},
        {"canonical_id": "ent_ben2", "entity_type": "person", "canonical_name": "Ben Turner",
         "aliases": ["ben"], "handles": [], "emails": [], "mention_count": 1,
         "source_types": ["slack"]},
    ])
    by_label, _ = loader.build_node_rows(frame)
    ben_ids = {row["id"] for row in by_label["Alias"] if row["name"] == "ben"}
    assert len(ben_ids) == 2, "the two Bens must not share one Alias node"


def test_documents_do_not_get_alias_nodes():
    by_label, _ = loader.build_node_rows(_entities_frame())
    assert not any(row["name"] == "a doc" for row in by_label.get("Alias", []))


def test_entity_types_map_to_ontology_labels():
    by_label, _ = loader.build_node_rows(_entities_frame())
    assert "Person" in by_label and "Team" in by_label and "Document" in by_label


def _edges_frame() -> pd.DataFrame:
    now = datetime(2026, 8, 19, tzinfo=UTC)
    return pd.DataFrame([
        {"edge_id": "edge_current", "src_canonical_id": "ent_sam",
         "dst_canonical_id": "ent_team", "rel_type": "MEMBER_OF",
         "stated_at": now, "ingested_at": now, "valid_from": now, "valid_to": None,
         "source_type": "slack", "source_doc_ids": ["dsid_abc"], "confidence": 1.0,
         "contested": False, "superseded_by": None},
        {"edge_id": "edge_old", "src_canonical_id": "ent_sam",
         "dst_canonical_id": "ent_team", "rel_type": "MEMBER_OF",
         "stated_at": now, "ingested_at": now, "valid_from": now,
         "valid_to": datetime(2026, 3, 15, tzinfo=UTC),
         "source_type": "slack", "source_doc_ids": ["dsid_abc"], "confidence": 1.0,
         "contested": False, "superseded_by": "edge_current"},
    ])


def test_is_current_is_explicit_because_is_null_is_unqueryable():
    """The whole bi-temporal design depends on this: `WHERE x IS NULL` is not
    supported, so "currently true" must be a stored boolean."""
    label_of = {"ent_sam": "Person", "ent_team": "Team"}
    groups = loader.build_edge_rows(_edges_frame(), label_of)
    rows = groups[("Person", "MEMBER_OF", "Team")]
    current = next(r for r in rows if r["edge_id"] == "edge_current")
    old = next(r for r in rows if r["edge_id"] == "edge_old")

    assert current["is_current"] is True
    assert current["valid_to_ts"] == FAR_FUTURE_TS
    assert old["is_current"] is False
    assert old["valid_to_ts"] < FAR_FUTURE_TS
    assert old["superseded_by"] == "edge_current"


def test_edges_group_by_endpoint_labels_and_rel_type():
    """A relationship batch takes exactly one literal label per endpoint and one
    literal type, so that triple is the unit of work."""
    label_of = {"ent_sam": "Person", "ent_team": "Team"}
    groups = loader.build_edge_rows(_edges_frame(), label_of)
    assert list(groups) == [("Person", "MEMBER_OF", "Team")]


def test_edges_with_unknown_endpoints_are_dropped_not_guessed():
    groups = loader.build_edge_rows(_edges_frame(), {"ent_sam": "Person"})
    assert groups == {}


def test_edge_rows_carry_a_separate_edge_id_alongside_the_reserved_id():
    """`id` is the relationship's reserved identity and cannot be projected in a
    RETURN, so the readable key has to be its own property."""
    label_of = {"ent_sam": "Person", "ent_team": "Team"}
    rows = loader.build_edge_rows(_edges_frame(), label_of)[("Person", "MEMBER_OF", "Team")]
    assert all(isinstance(r["id"], int) and isinstance(r["edge_id"], str) for r in rows)


def test_surrogate_collisions_are_fatal_not_silent():
    """A collision would merge two unrelated entities into one node — the exact
    failure this project exists to avoid."""
    rows = {"Person": [
        {"id": 1, "canonical_id": "ent_a"},
        {"id": 1, "canonical_id": "ent_b"},
    ]}
    with pytest.raises(SystemExit):
        loader._check_collisions(rows)


# ---------------------------------------------------------------------------
# integration — needs a live node
# ---------------------------------------------------------------------------
def _client_or_skip():
    from src.graph.client import GraphClient

    client = GraphClient()
    try:
        client.cypher("MATCH (n:Person) RETURN count(*) AS c")
    except Exception:
        client.close()
        pytest.skip("HydraDB is not running (just db-up)")
    return client


@pytest.fixture
def client():
    c = _client_or_skip()
    yield c
    c.close()


def _skip_if_empty(client):
    rows = client.cypher("MATCH (n:Person) RETURN count(*) AS c")
    if not rows or not rows[0]["c"]:
        pytest.skip("graph is empty (just load)")


def test_find_entity_resolves_a_canonical_name(client):
    _skip_if_empty(client)
    rows = client.cypher("MATCH (n:Person) RETURN n.name AS name")
    name = next(r["name"] for r in rows if r["name"])
    assert any(e.canonical_name == name for e in client.find_entity(name))


def test_find_entity_is_alias_aware(client):
    """The submission's headline capability: a surface form that is not the
    canonical name still resolves to the right entity."""
    _skip_if_empty(client)
    rows = client.cypher(
        "MATCH (p:Person)-[:HAS_ALIAS]->(a:Alias) "
        "RETURN p.canonical_id AS cid, a.name AS alias, p.name AS name")
    hit = next((r for r in rows if r["alias"] and r["alias"] != r["name"]), None)
    if hit is None:
        pytest.skip("no non-canonical alias in the loaded graph")
    assert hit["cid"] in {e.canonical_id for e in client.find_entity(hit["alias"])}


def test_find_entity_is_case_insensitive(client):
    _skip_if_empty(client)
    rows = client.cypher("MATCH (n:Person) RETURN n.name AS name")
    name = next(r["name"] for r in rows if r["name"] and r["name"].lower() != r["name"])
    assert client.find_entity(name.lower()) and client.find_entity(name.upper())


def test_facts_about_returns_superseded_edges_too(client):
    """Conflict answers need both sides, so this must not filter to current."""
    _skip_if_empty(client)
    rows = client.cypher(
        "MATCH (s:Person)-[e:MEMBER_OF]->(d:Team) WHERE e.is_current = false "
        "RETURN e.src_canonical_id AS cid")
    if not rows:
        pytest.skip("no superseded MEMBER_OF edge in the loaded graph")
    edges = client.facts_about(rows[0]["cid"], "MEMBER_OF")
    assert any(e.valid_to is not None for e in edges), "superseded edge missing"
    assert any(e.valid_to is None for e in edges), "current edge missing"
    assert edges[0].valid_to is None, "current edge should sort first"


def test_edges_carry_provenance(client):
    _skip_if_empty(client)
    rows = client.cypher("MATCH (s:Person)-[e:MEMBER_OF]->(d:Team) "
                         "RETURN e.src_canonical_id AS cid")
    if not rows:
        pytest.skip("no MEMBER_OF edges loaded")
    edges = client.facts_about(rows[0]["cid"], "MEMBER_OF")
    assert edges and all(e.source_doc_ids for e in edges), \
        "every edge must cite the documents behind it"


def test_paths_uses_native_traversal_and_returns_provenance(client):
    """algo.SSpaths / SPpaths are the graph-native core of the submission."""
    _skip_if_empty(client)
    rows = client.cypher("MATCH (s:Person)-[e:POSTED_IN]->(d:Channel) "
                         "RETURN e.src_canonical_id AS cid")
    if not rows:
        pytest.skip("no POSTED_IN edges loaded")
    paths = client.paths([rows[0]["cid"]], [], max_len=2)
    assert paths, "SSpaths returned nothing"
    assert any(p.length >= 1 for p in paths)
    assert any(p.doc_ids for p in paths), "paths must carry citable documents"


def test_paths_between_two_entities(client):
    _skip_if_empty(client)
    rows = client.cypher("MATCH (s:Person)-[e:POSTED_IN]->(d:Channel) "
                         "RETURN e.src_canonical_id AS s, e.dst_canonical_id AS d")
    if not rows:
        pytest.skip("no POSTED_IN edges loaded")
    assert client.paths([rows[0]["s"]], [rows[0]["d"]], max_len=2)


def test_alias_edges_are_excluded_from_paths(client):
    """HAS_ALIAS is structural. Traversing it would connect unrelated entities
    through a shared surface form."""
    _skip_if_empty(client)
    rows = client.cypher("MATCH (p:Person)-[:HAS_ALIAS]->(a:Alias) "
                         "RETURN p.canonical_id AS cid")
    if not rows:
        pytest.skip("no aliases loaded")
    for path in client.paths([rows[0]["cid"]], [], max_len=2):
        assert all(step.edge.rel_type != "HAS_ALIAS" for step in path.steps)
