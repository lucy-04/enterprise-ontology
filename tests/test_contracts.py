"""Guard the frozen inter-track contract (CLAUDE.md §12).

If these fail, one track has changed a shape the other depends on. That is
exactly the breakage this suite exists to catch early, so fix the change rather
than the test — unless both people agreed to move the contract.
"""

from __future__ import annotations

from datetime import datetime

from src.common.schemas import (
    SOURCE_TYPES,
    AnswerResult,
    Edge,
    Entity,
    Mention,
    NormalizedDoc,
    Relation,
)


def test_nine_sources_and_drive_spelling():
    assert len(SOURCE_TYPES) == 9
    # questions.jsonl spells it google_drive; a mismatch here silently drops
    # 60 questions' worth of source filtering.
    assert "google_drive" in SOURCE_TYPES
    assert "drive" not in SOURCE_TYPES


def test_parquet_columns_match_dataclass_fields():
    """On-disk column lists must match the dataclasses, or the two tracks will
    write and read different layouts."""
    for cls in (NormalizedDoc, Mention, Relation, Entity, Edge):
        fields = set(cls.__dataclass_fields__)
        assert set(cls.PARQUET_COLUMNS) == fields, (
            f"{cls.__name__}: PARQUET_COLUMNS drifted from the dataclass fields"
        )


def test_provenance_fields_exist():
    """Every extracted fact must carry the document it came from — the scorer
    checks cited documents, so a missing doc_id is a lost point."""
    assert "doc_id" in Mention.__dataclass_fields__
    assert "doc_id" in Relation.__dataclass_fields__
    assert "source_doc_ids" in Edge.__dataclass_fields__


def test_edge_is_bitemporal_and_non_destructive():
    """The conflict model invalidates, never deletes (CLAUDE.md §11 B4)."""
    for field in ("stated_at", "ingested_at", "valid_from", "valid_to",
                  "contested", "superseded_by"):
        assert field in Edge.__dataclass_fields__

    live = Edge(
        edge_id="e1", src_canonical_id="p1", dst_canonical_id="t1",
        rel_type="OWNS", stated_at=None, ingested_at=datetime.now(),
        valid_from=None, valid_to=None, source_type="jira",
    )
    assert live.is_current

    stale = Edge(
        edge_id="e0", src_canonical_id="p0", dst_canonical_id="t1",
        rel_type="OWNS", stated_at=None, ingested_at=datetime.now(),
        valid_from=None, valid_to=datetime.now(), source_type="slack",
        superseded_by="e1",
    )
    assert not stale.is_current
    assert stale.superseded_by == "e1"


def test_entity_merge_keeps_every_surface_form():
    """Non-destructive merge is the demo (CLAUDE.md §6)."""
    e = Entity(
        canonical_id="p1", entity_type="Person", canonical_name="Soham Ratnaparkhi",
        aliases=["Sam", "@soham", "S. Ratnaparkhi"],
    )
    for surface in ("Sam", "@soham", "S. Ratnaparkhi"):
        assert surface in e.aliases


def test_answer_jsonl_shape_matches_benchmark():
    """The exact line format the scorer expects (CLAUDE.md §3.1)."""
    out = AnswerResult(
        question_id="qst_0001", answer="42", document_ids=["dsid_abc"],
    ).to_answer_jsonl()
    assert set(out) == {"question_id", "answer", "document_ids"}
    assert out["document_ids"] == ["dsid_abc"]
