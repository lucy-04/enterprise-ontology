"""Regression tests for B1 rule-based extraction (Track B).

These pin the behaviours that took real debugging to get right, so a future
change that reintroduces a known bug fails loudly:
  - prose labels ("Steps to reproduce (staging):") must NOT become people
  - artifact nodes use their natural key as surface_form (deterministic joins)
  - every mention/relation carries a doc_id (provenance is non-negotiable)
  - the ontology only contains declared types
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from src.common.schemas import NormalizedDoc
from src.extract.base import valid_entity_types, valid_rel_types
from src.extract.classify import classify, is_bot, is_internal_email, is_proper_name
from src.extract.run import extract_frame
from src.extract.sources import get_extractor

FIXTURE = "tests/fixtures/normalized_sample.parquet"


def _doc(**kw):
    base = dict(doc_id="dsid_test", source_type="jira", title="t", body="",
                timestamp=datetime(2026, 3, 1), author_refs=[], mention_refs=[],
                thread_id=None, path="", raw_metadata={})
    base.update(kw)
    return NormalizedDoc(**base)


# -- classification --------------------------------------------------------

def test_bots_are_not_people():
    for b in ["deploy-bot", "incident-bot", "OpsPlaybot", "IncidentBot"]:
        assert is_bot(b), b
        assert classify(b) == "bot"


def test_redwood_is_internal_both_tlds():
    assert is_internal_email("a@redwood.ai")
    assert is_internal_email("karthik_iyer@redwood.com")
    assert not is_internal_email("amal@greenlinehealth.com")


def test_prose_labels_are_not_proper_names():
    for junk in ["Steps to reproduce", "Next steps", "Auto-summary", "Impact"]:
        assert not is_proper_name(junk), junk
    for name in ["Aisha", "Mei Chen", "Grace O'Connor"]:
        assert is_proper_name(name), name


# -- extractor behaviour ---------------------------------------------------

def test_paren_parser_skips_prose_labels():
    doc = _doc(body="Steps to reproduce (staging): do the thing\n"
                    "Support (Aisha): we saw the 5xx spike\n")
    res = get_extractor("jira").extract_doc(doc)
    people = {m.surface_form for m in res.mentions if m.entity_type == "person"}
    assert "Aisha" in people
    assert "Steps to reproduce" not in people
    assert "staging" not in people


def test_ticket_uses_natural_key_as_surface_form():
    doc = _doc(source_type="jira",
               raw_metadata={"slug": "SUP-359481-thing", "cross_refs": "ENG-42,PR#7"})
    res = get_extractor("jira").extract_doc(doc)
    tickets = {m.surface_form for m in res.mentions if m.entity_type == "ticket"}
    assert "SUP-359481" in tickets   # the doc's own ticket, from the slug
    assert "ENG-42" in tickets       # a cross-referenced ticket -> joins across docs


def test_every_candidate_has_a_doc_id():
    df = pd.read_parquet(FIXTURE)
    mentions, relations = extract_frame(df)
    assert mentions and relations
    assert all(m.doc_id for m in mentions)
    assert all(r.doc_id for r in relations)


def test_only_declared_types_are_emitted():
    df = pd.read_parquet(FIXTURE)
    mentions, relations = extract_frame(df)
    ents, rels = valid_entity_types(), valid_rel_types()
    assert {m.entity_type for m in mentions} <= ents
    assert {r.rel_type for r in relations} <= rels


def test_all_nine_sources_produce_mentions():
    df = pd.read_parquet(FIXTURE)
    mentions, _ = extract_frame(df)
    by_src = {m.source_type for m in mentions}
    assert by_src == set(df.source_type.unique())
