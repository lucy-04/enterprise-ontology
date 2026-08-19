"""Regression tests for B3 entity resolution (Track B).

Pins the behaviours that were hard-won:
  - the name<->email bridge merges "Karthik Iyer" with "karthik_iyer@redwood.com"
  - but does NOT merge two different people who share a first name (Ben Carter
    vs Ben Turner) — the precision bug that cost real debugging
  - artifacts resolve by exact natural key (deterministic cross-source join)
  - documents resolve to their own doc_id, never merged by title
The Splink model itself is not asserted on (it's probabilistic + slow); these
target the deterministic machinery around it.
"""

from __future__ import annotations

import pandas as pd

from src.resolve.base import handle_stem, normalize_name, surname_key
from src.resolve.splink_er import (
    _UnionFind,
    _name_locals,
    bridge_name_email_handle,
    build_person_frame,
)
from src.resolve.run import resolve


def _mention(mid, surface, etype, source="gmail", doc="dsid_x"):
    return {"mention_id": mid, "doc_id": doc, "source_type": source,
            "surface_form": surface, "entity_type": etype,
            "context_snippet": "", "extractor": "rule", "confidence": 1.0,
            "timestamp": None}


# -- name-local generation -------------------------------------------------

def test_name_locals_are_multicomponent_only():
    locs = _name_locals("karthik iyer")
    assert "karthik_iyer" in locs
    assert "karthik.iyer" in locs
    # bare first/last are excluded on purpose (they over-merge)
    assert "karthik" not in locs
    assert "iyer" not in locs


def test_single_token_name_has_no_locals():
    assert _name_locals("ben") == set()


# -- the bridge ------------------------------------------------------------

def test_bridge_merges_name_and_email():
    df = pd.DataFrame([
        _mention("m1", "Karthik Iyer", "person", source="slack"),
        _mention("m2", "karthik_iyer@redwood.com", "person", source="gmail"),
    ])
    frame = build_person_frame(df)
    uf = _UnionFind()
    for u in frame["unique_id"]:
        uf.find(u)
    assert bridge_name_email_handle(frame, uf) == 1
    assert uf.find("m1") == uf.find("m2")


def test_bridge_does_not_merge_different_people_sharing_first_name():
    df = pd.DataFrame([
        _mention("m1", "Ben Carter", "person"),
        _mention("m2", "Ben Turner", "person"),
        _mention("m3", "ben_carter@redwood.ai", "person"),
    ])
    frame = build_person_frame(df)
    uf = _UnionFind()
    for u in frame["unique_id"]:
        uf.find(u)
    bridge_name_email_handle(frame, uf)
    assert uf.find("m1") == uf.find("m3")     # Ben Carter <-> his email: merged
    assert uf.find("m2") != uf.find("m1")     # Ben Turner: stays separate


# -- deterministic resolution ---------------------------------------------

def test_same_ticket_id_across_docs_is_one_entity():
    df = pd.DataFrame([
        _mention("m1", "ENG-30521", "ticket", source="jira", doc="dsid_a"),
        _mention("m2", "ENG-30521", "ticket", source="slack", doc="dsid_b"),
    ])
    entities, clusters = resolve(df)
    tickets = [e for e in entities if e.entity_type == "ticket"]
    assert len(tickets) == 1
    assert tickets[0].mention_count == 2
    assert set(tickets[0].source_types) == {"jira", "slack"}


def test_documents_keyed_by_doc_id_not_title():
    df = pd.DataFrame([
        _mention("m1", "Weekly Sync", "document", doc="dsid_a"),
        _mention("m2", "Weekly Sync", "document", doc="dsid_b"),  # same title, diff doc
    ])
    entities, _ = resolve(df)
    docs = [e for e in entities if e.entity_type == "document"]
    assert len(docs) == 2
    assert {e.canonical_id for e in docs} == {"dsid_a", "dsid_b"}
