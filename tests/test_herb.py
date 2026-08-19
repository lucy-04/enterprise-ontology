"""Regression tests for B7 HERB spot-check (Track B).

Unit-test the entity-resolution core (co-occurrence disambiguation + scoring)
with synthetic data, so CI needs no HERB download. A full run is exercised
separately only when data/herb/ is already present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.resolve.herb_check import disambiguate, eid_evidence, prf, transcript_names


# -- the ER core: disambiguation by co-occurrence --------------------------

def test_unique_name_resolves_directly():
    name2eids = {"Alice Brown": ["eid_1"]}
    eid, ambiguous = disambiguate("Alice Brown", name2eids, context_eids=set())
    assert eid == "eid_1" and ambiguous is False


def test_shared_name_resolved_by_context():
    # three Hannah Taylors; only eid_2 appears in this product's Slack -> pick it
    name2eids = {"Hannah Taylor": ["eid_1", "eid_2", "eid_3"]}
    eid, ambiguous = disambiguate("Hannah Taylor", name2eids, context_eids={"eid_2"})
    assert eid == "eid_2" and ambiguous is True


def test_shared_name_unresolvable_when_context_is_ambiguous():
    name2eids = {"Hannah Taylor": ["eid_1", "eid_2", "eid_3"]}
    # two candidates co-occur -> cannot choose -> leave unresolved (don't guess)
    eid, ambiguous = disambiguate("Hannah Taylor", name2eids, context_eids={"eid_1", "eid_2"})
    assert eid is None and ambiguous is True
    # none co-occur -> also unresolved
    eid2, _ = disambiguate("Hannah Taylor", name2eids, context_eids={"eid_9"})
    assert eid2 is None


# -- scoring ---------------------------------------------------------------

def test_prf_perfect_and_partial():
    assert prf({"a", "b"}, {"a", "b"}) == (1.0, 1.0, 1.0)
    p, r, f = prf({"a", "b", "c"}, {"a", "b"})   # one false positive
    assert r == 1.0 and round(p, 2) == 0.67


# -- artifact parsing ------------------------------------------------------

def test_eid_and_name_extraction():
    product = {
        "slack": [{"Message": {"User": {"userId": "eid_aaa"},
                               "text": "hey @'eid_bbb' look here"}}],
        "meeting_transcripts": [{"transcript":
            "Attendees\nAlice Brown, Bob Smith\nTranscript\nAlice Brown: hi\n"}],
    }
    assert eid_evidence(product) == {"eid_aaa", "eid_bbb"}
    assert {"Alice Brown", "Bob Smith"} <= transcript_names(product)


# -- optional full run (only if HERB is downloaded) ------------------------

@pytest.mark.skipif(not (Path("data/herb/products").exists()
                         and any(Path("data/herb/products").glob("*.json"))),
                    reason="HERB not downloaded")
def test_full_run_recovers_reasonable_recall():
    from src.resolve.herb_check import run
    result = run(limit=3)
    assert result["products"] >= 1
    assert result["mean_recall"] > 0.5    # artifacts-only recovery should clear 50%
