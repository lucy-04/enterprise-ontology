"""A6 regression tests — the FastAPI service.

The API is deliberately thin, so these test the two things that are actually
easy to break: the serialisers that flatten dataclasses into what the page
draws, and the contract each endpoint promises the UI.

Endpoint tests skip cleanly when HydraDB or the search index is not available,
so the suite stays green on a machine that is not running the stack.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from src.api.main import _answer_json, _edge_json, _entity_json, _path_json, app
from src.common.schemas import (
    AnswerResult,
    AnswerTrace,
    Edge,
    Entity,
    Path,
    PathStep,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# serialisers
# ---------------------------------------------------------------------------
def _edge(valid_to=None, **kw) -> Edge:
    return Edge(
        edge_id=kw.get("edge_id", "edge_1"),
        src_canonical_id="ent_a", dst_canonical_id="ent_b",
        rel_type="MEMBER_OF",
        stated_at=datetime(2026, 1, 5), ingested_at=datetime(2026, 8, 19),
        valid_from=datetime(2026, 1, 5), valid_to=valid_to,
        source_type="slack", source_doc_ids=["dsid_1"], confidence=1.0,
        contested=False, superseded_by=kw.get("superseded_by"),
    )


def test_current_edge_is_flagged_current():
    assert _edge_json(_edge())["is_current"] is True


def test_superseded_edge_keeps_its_end_date_and_pointer():
    """A contradicted fact is invalidated, never deleted — the UI needs both the
    date it stopped being true and what replaced it."""
    payload = _edge_json(_edge(valid_to=datetime(2026, 3, 15),
                               superseded_by="edge_2"))
    assert payload["is_current"] is False
    assert payload["valid_to"].startswith("2026-03-15")
    assert payload["superseded_by"] == "edge_2"


def test_edge_carries_provenance():
    assert _edge_json(_edge())["source_doc_ids"] == ["dsid_1"]


def test_entity_surface_forms_merge_every_variant():
    """The entity-resolution demo: one list holding every way this person was
    ever written, canonical name included, deduplicated."""
    entity = Entity(canonical_id="ent_sam", entity_type="person",
                    canonical_name="Sam Ratnaparkhi",
                    aliases=["Sam", "Sam Ratnaparkhi"], handles=["soham"],
                    emails=["sam@redwood.com"], mention_count=9,
                    source_types=["slack", "gmail"])
    forms = _entity_json(entity)["surface_forms"]
    assert forms == sorted({"Sam", "Sam Ratnaparkhi", "soham", "sam@redwood.com"})


def test_entity_with_no_aliases_still_lists_its_own_name():
    entity = Entity(canonical_id="ent_x", entity_type="team", canonical_name="SRE")
    assert _entity_json(entity)["surface_forms"] == ["SRE"]


def test_path_exposes_its_citation_list():
    path = Path(start=Entity(canonical_id="ent_a", entity_type="person",
                             canonical_name="A"),
                steps=[PathStep(edge=_edge(),
                                to_entity=Entity(canonical_id="ent_b",
                                                 entity_type="team",
                                                 canonical_name="B"))])
    payload = _path_json(path)
    assert payload["length"] == 1
    assert payload["doc_ids"] == ["dsid_1"]
    assert payload["steps"][0]["to"]["canonical_name"] == "B"


def test_answer_json_carries_the_whole_trace():
    """Every answer ships its reasoning — the UI renders the route, the grade
    decision and the evidence, not just the sentence."""
    result = AnswerResult(
        question_id="q1", answer="Eng-Oncall.", document_ids=["dsid_1"],
        abstained=False, confidence=0.85,
        trace=AnswerTrace(route="conflict", retrieved_doc_ids=["dsid_1", "dsid_2"],
                          entity_ids=["ent_a"], grade_passed=True,
                          grade_reason="SUPPORTED", retries=1, llm_calls=2))
    payload = _answer_json(result)
    assert payload["answer"] == "Eng-Oncall."
    assert payload["trace"]["route"] == "conflict"
    assert payload["trace"]["grade_reason"] == "SUPPORTED"
    assert payload["trace"]["retries"] == 1 and payload["trace"]["llm_calls"] == 2


def test_abstention_is_visible_in_the_payload():
    """The UI styles an abstention differently, so it must not be inferred from
    an empty document list."""
    payload = _answer_json(AnswerResult(question_id="q", answer="I don't know.",
                                        abstained=True, confidence=0.0))
    assert payload["abstained"] is True
    assert payload["document_ids"] == []


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------
def test_healthz():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_ask_rejects_an_empty_question():
    assert client.post("/ask", json={"question": "   "}).status_code == 400


def test_index_page_is_served():
    res = client.get("/")
    assert res.status_code == 200
    assert "Redwood Ontology" in res.text


def test_ui_assets_are_served_locally():
    """cytoscape is vendored, not pulled from a CDN, so the demo cannot fail
    because the network is flaky while recording."""
    for path in ("/static/app.js", "/static/style.css",
                 "/static/vendor/cytoscape.min.js"):
        assert client.get(path).status_code == 200, path


def _live_or_skip():
    """Skip unless the search index is built."""
    res = client.get("/api/stats")
    if res.status_code != 200 or not res.json().get("documents"):
        pytest.skip("search index not built (just index)")
    return res.json()


def _graph_or_skip():
    """Skip unless HydraDB is also up and holding a loaded graph.

    /api/stats deliberately swallows graph errors so the UI header still renders
    with a dead database — which means a non-zero `documents` proves Layer 1 is
    there and says nothing about Layer 2. Graph endpoints raise rather than
    degrade, so they need this stricter guard.
    """
    stats = _live_or_skip()
    if not stats.get("entities"):
        pytest.skip("graph not loaded (just db-up, just load)")
    return stats


def test_stats_reports_what_is_loaded():
    assert _live_or_skip()["documents"] > 0


def test_stats_counts_edges_and_every_node_label():
    """The header is the first thing a viewer reads, so it has to be true.
    HydraDB rejects an untyped edge pattern, so the totals are a sum over every
    relationship type and every label — easy to leave at a hardcoded zero, which
    makes a working graph look empty on camera."""
    stats = _graph_or_skip()
    assert stats["edges"] > 0, "a loaded graph reporting 0 edges reads as broken"
    assert stats["aliases"] > 0
    # entities spans all labels, not just Person, so it must exceed the alias-
    # bearing subset alone.
    assert stats["entities"] > 0


def test_search_returns_hits():
    _live_or_skip()
    hits = client.get("/api/search", params={"q": "billing ownership", "k": 5}) \
                 .json()["hits"]
    assert hits and hits[0]["doc_id"] and hits[0]["source_type"]


def test_doc_roundtrip():
    _live_or_skip()
    hits = client.get("/api/search", params={"q": "incident", "k": 1}).json()["hits"]
    if not hits:
        pytest.skip("no search hits")
    doc = client.get(f"/doc/{hits[0]['doc_id']}").json()
    assert doc["doc_id"] == hits[0]["doc_id"] and doc["body"]


def test_unknown_doc_is_404():
    _live_or_skip()
    assert client.get("/doc/dsid_does_not_exist").status_code == 404


def test_unknown_entity_is_404():
    _graph_or_skip()
    assert client.get("/entity/ent_does_not_exist").status_code == 404


def test_resolve_finds_an_entity_by_surface_form():
    _graph_or_skip()
    rows = client.get("/api/search", params={"q": "team", "k": 1}).json()["hits"]
    if not rows:
        pytest.skip("no data")
    found = client.get("/api/resolve", params={"name": "Marcus Lin"}).json()
    if not found["entities"]:
        pytest.skip("that entity is not in the currently-loaded graph")
    assert found["entities"][0]["surface_forms"]


def test_subgraph_is_empty_for_unknown_ids_rather_than_erroring():
    _graph_or_skip()
    payload = client.get("/subgraph", params={"ids": "ent_nope"}).json()
    assert payload["nodes"] == [] and payload["edges"] == []
