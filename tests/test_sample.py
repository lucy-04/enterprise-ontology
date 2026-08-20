"""A1 — the corpus sampler that draws Layer 2's graph-sized subset.

Layer 1 indexes every document; the graph is built over a sample, so the
sample has to be representative or nothing measured on it carries over.
"""

from __future__ import annotations

from src.ingest.sample import quota


def test_quota_is_proportional_to_the_real_corpus_mix():
    counts = {"slack": 285605, "gmail": 121390, "confluence": 5189}
    plan = quota(counts, 2000)
    assert sum(plan.values()) == 2000
    assert plan["slack"] > plan["gmail"] > plan["confluence"]

def test_every_source_survives_a_small_sample():
    counts = {"slack": 285605, "confluence": 5189, "fireflies": 10173}
    plan = quota(counts, 50)
    assert all(v >= 1 for v in plan.values())
    assert sum(plan.values()) == 50

def test_quota_never_asks_for_more_than_a_source_has():
    """When the requested sample approaches the corpus size, a source's
    proportional share can exceed the documents it actually has."""
    plan = quota({"tiny": 3, "big": 10}, 100)
    assert plan["tiny"] == 3 and plan["big"] == 10

def test_sample_smaller_than_source_count_still_covers_every_source():
    counts = {f"s{i}": 1000 for i in range(9)}
    plan = quota(counts, 5)
    assert all(v >= 1 for v in plan.values())
