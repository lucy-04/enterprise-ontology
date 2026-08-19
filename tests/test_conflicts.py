"""Regression tests for B4 conflict + bi-temporal edge model (Track B).

Pins the behaviours that matter for the "conflict resolution" submission
requirement and that were tricky to get right:
  - duplicate assertions of one fact collapse into one edge that keeps every doc
  - a single-valued relation with a newer contradicting value supersedes the old
    one (valid_to + superseded_by set), never deletes it
  - coexisting facts (same source, same/unknown date) are NOT called a conflict
  - multi-valued relations never conflict
  - OWNS is constrained on the target side (a thing has one owner)
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from src.conflicts.run import build_edges


def _rel(rid, src, dst, rel, doc, source, stated, conf=1.0):
    return {"relation_id": rid, "src_mention_id": src, "dst_mention_id": dst,
            "rel_type": rel, "doc_id": doc, "source_type": source,
            "stated_at": stated, "evidence_snippet": "", "extractor": "rule",
            "confidence": conf}


def _cluster(mention_id, cid):
    return {"canonical_id": cid, "mention_id": mention_id,
            "match_probability": 1.0, "method": "exact"}


def test_duplicate_fact_collapses_and_keeps_all_docs():
    rels = pd.DataFrame([
        _rel("r1", "mA", "mT", "POSTED_IN", "doc1", "slack", None),
        _rel("r2", "mA2", "mT2", "POSTED_IN", "doc2", "slack", None),
    ])
    clusters = pd.DataFrame([
        _cluster("mA", "p1"), _cluster("mT", "c1"),
        _cluster("mA2", "p1"), _cluster("mT2", "c1"),
    ])
    edges = build_edges(rels, clusters)
    assert len(edges) == 1
    assert set(edges[0].source_doc_ids) == {"doc1", "doc2"}


def test_newer_membership_supersedes_older():
    rels = pd.DataFrame([
        _rel("r1", "mA", "mOld", "MEMBER_OF", "doc1", "slack", datetime(2026, 1, 1)),
        _rel("r2", "mA2", "mNew", "MEMBER_OF", "doc2", "slack", datetime(2026, 6, 1)),
    ])
    clusters = pd.DataFrame([
        _cluster("mA", "person"), _cluster("mOld", "teamOld"),
        _cluster("mA2", "person"), _cluster("mNew", "teamNew"),
    ])
    edges = {(e.src_canonical_id, e.rel_type, e.dst_canonical_id): e for e in build_edges(rels, clusters)}
    old = edges[("person", "MEMBER_OF", "teamOld")]
    new = edges[("person", "MEMBER_OF", "teamNew")]
    assert old.valid_to is not None and old.superseded_by == new.edge_id
    assert new.valid_to is None            # current
    assert new.is_current and not old.is_current


def test_coexisting_facts_are_not_a_conflict():
    # same person, two teams, same source, no dates -> cannot order -> keep both
    rels = pd.DataFrame([
        _rel("r1", "mA", "mT1", "MEMBER_OF", "doc1", "slack", None),
        _rel("r2", "mA2", "mT2", "MEMBER_OF", "doc2", "slack", None),
    ])
    clusters = pd.DataFrame([
        _cluster("mA", "person"), _cluster("mT1", "team1"),
        _cluster("mA2", "person"), _cluster("mT2", "team2"),
    ])
    edges = build_edges(rels, clusters)
    assert all(e.valid_to is None for e in edges)     # both current
    assert not any(e.contested for e in edges)


def test_multivalued_relation_never_conflicts():
    # a doc referencing two tickets is not a contradiction
    rels = pd.DataFrame([
        _rel("r1", "mD", "mT1", "REFERENCES", "doc1", "jira", datetime(2026, 1, 1)),
        _rel("r2", "mD", "mT2", "REFERENCES", "doc1", "jira", datetime(2026, 6, 1)),
    ])
    clusters = pd.DataFrame([
        _cluster("mD", "doc"), _cluster("mT1", "t1"), _cluster("mT2", "t2"),
    ])
    edges = build_edges(rels, clusters)
    assert all(e.valid_to is None for e in edges)


def test_owns_is_constrained_on_target_side():
    # one team owns two docs -> NOT a conflict (owner owns many things)
    rels = pd.DataFrame([
        _rel("r1", "mTeam", "mDoc1", "OWNS", "doc1", "confluence", None),
        _rel("r2", "mTeam2", "mDoc2", "OWNS", "doc2", "confluence", None),
    ])
    clusters = pd.DataFrame([
        _cluster("mTeam", "team"), _cluster("mDoc1", "d1"),
        _cluster("mTeam2", "team"), _cluster("mDoc2", "d2"),
    ])
    edges = build_edges(rels, clusters)
    assert all(e.valid_to is None for e in edges)     # no false conflict

    # but one doc owned by a low- and a high-priority source -> the higher wins
    rels2 = pd.DataFrame([
        _rel("r1", "mT1", "mDoc", "OWNS", "doc1", "slack", datetime(2026, 1, 1)),
        _rel("r2", "mT2", "mDoc", "OWNS", "doc2", "jira", datetime(2026, 1, 1)),
    ])
    clusters2 = pd.DataFrame([
        _cluster("mT1", "teamA"), _cluster("mDoc", "page"),
        _cluster("mT2", "teamB"), _cluster("mDoc", "page"),
    ])
    edges2 = {(e.src_canonical_id): e for e in build_edges(rels2, clusters2)}
    assert edges2["teamA"].valid_to is not None       # slack owner superseded
    assert edges2["teamB"].valid_to is None           # jira owner wins (higher priority)
