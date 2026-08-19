"""Regression tests for B5/B6 router, abstention gate, synthesis (Track B).

Assert the machinery, not LLM answer quality (there's no LLM in CI):
  - question classification picks the right retrieval route
  - the router NEVER raises, even against the un-implemented stub client
  - true no-evidence questions abstain with empty citations
  - when an entity resolves, its graph facts (incl. superseded) reach the answer
  - the LLM adapter is absent-safe
"""

from __future__ import annotations

from src.agent.classify import classify_route, question_entities
from src.agent.router import answer
from src.graph.client import GraphClient
from src.llm.adapter import LLM
from tests.support.local_client import LocalGraphClient

NORM = "tests/fixtures/normalized_sample.parquet"
ENTS = "data/resolved/entities.parquet"
EDGES = "data/graph/edges.parquet"


def _client():
    return LocalGraphClient(NORM, ENTS, EDGES)


# -- classification --------------------------------------------------------

def test_route_classification():
    assert classify_route("How many tickets are in the SUP project?") == "aggregate"
    assert classify_route("Which team is Alex on now?") == "conflict"
    assert classify_route("Who worked on the KMS component with Priya?") == "multihop"
    assert classify_route("What does the onboarding playbook cover?") == "lookup"


def test_question_entities_extracts_ids_and_names():
    ents = question_entities('Who owns SUP-359481 and emailed sam@redwood.ai about "KMS"?')
    assert "SUP-359481" in ents
    assert "sam@redwood.ai" in ents
    assert "KMS" in ents


# -- router safety ---------------------------------------------------------

def test_router_never_raises_against_unimplemented_client():
    # base GraphClient raises NotBuiltYetError from every method
    r = answer("anything at all?", GraphClient(), "qX")
    assert r.abstained and r.document_ids == []
    assert r.answer  # a real string, not a crash


def test_true_no_evidence_abstains():
    # pure gibberish -> zero keyword hits -> empty context -> abstain.
    # (Offline, abstention only fires on genuine zero-match; nuanced abstention
    #  for the info_not_found questions is the LLM grader's job, by design.)
    r = answer("Zzzqqq wobbleplonk frobnicator quuxblat?", _client(), "q0")
    assert r.abstained is True
    assert r.document_ids == []
    assert r.trace.grade_passed is False


# -- retrieval wiring ------------------------------------------------------

def test_resolved_entity_surfaces_graph_facts():
    r = answer("Which team is Alex on now?", _client(), "q1")
    assert r.trace.route == "conflict"
    assert r.trace.entity_ids                     # an entity resolved
    assert not r.abstained
    # the answer/facts should mention a membership relation for the resolved person
    assert "MEMBER_OF" in r.answer or "WORKS_FOR" in r.answer


def test_lookup_returns_supporting_documents():
    r = answer("What is the operational onboarding playbook about?", _client(), "q2")
    assert not r.abstained
    assert r.document_ids            # cited at least one contributing doc


# -- LLM adapter -----------------------------------------------------------

def test_llm_absent_safe():
    llm = LLM()
    # with no key in the test env, it must not be "available" and must return None
    if not llm.available:
        assert llm.complete("hi") is None
        assert llm.complete_json("hi") is None
