"""Regression tests for the A2 normalizers, against real fixture documents.

These encode what was actually measured in CLAUDE.md §7.4. If a parser change
drops slack speakers or lets prose labels through as people, these fail — both
are mistakes that were already made once during the build.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ingest.normalize import (
    DOC_ID_RE,
    NOT_A_SPEAKER,
    find_sources,
    parse_file,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sample_docs"

pytestmark = pytest.mark.skipif(
    not FIXTURES.is_dir(), reason="fixtures not present"
)


def _parse_all(source: str) -> list[dict]:
    files = sorted((FIXTURES / source).glob("*.txt"))
    return [r for r in (parse_file((str(f), source)) for f in files) if r]


def test_all_nine_sources_present():
    found = find_sources(FIXTURES)
    assert len(found) == 9, f"expected 9 sources, got {sorted(found)}"


def test_doc_id_uses_double_underscore():
    """Filenames are dsid_<32hex>__<slug>.txt — a single-underscore split
    silently truncates every doc_id and breaks matching against
    expected_doc_ids in questions.jsonl."""
    m = DOC_ID_RE.match("dsid_000aabb424694648b5651aa9a2438c81__some-slug-2028.txt")
    assert m and m.group(1) == "dsid_000aabb424694648b5651aa9a2438c81"


@pytest.mark.parametrize("source", [
    "slack", "gmail", "jira", "confluence", "fireflies",
    "hubspot", "github", "linear", "google_drive",
])
def test_every_doc_parses_with_required_fields(source):
    recs = _parse_all(source)
    assert len(recs) == 20
    for r in recs:
        assert r["doc_id"].startswith("dsid_")
        assert r["source_type"] == source
        assert r["title"], "title (line 1) must never be empty"


def test_gmail_extracts_names_and_emails():
    """Gmail is the highest-confidence Person source — real RFC headers."""
    recs = _parse_all("gmail")
    with_authors = [r for r in recs if r["author_refs"]]
    assert len(with_authors) >= 18
    all_refs = " ".join(ref for r in recs for ref in r["author_refs"])
    assert "@" in all_refs, "expected email addresses among gmail author_refs"


def test_slack_finds_speakers_in_the_bare_form():
    """MEASURED: only ~2/20 slack docs use `handle (team):`; ~18 use a bare
    `speaker:`. A regex that only accepts the parenthesised form loses ~90%
    of speakers — that regression happened once already."""
    recs = _parse_all("slack")
    empty = [r for r in recs if not r["author_refs"]]
    assert not empty, f"{len(empty)} slack docs yielded no speakers"
    assert sum(len(r["author_refs"]) for r in recs) / len(recs) >= 3


def test_prose_labels_are_not_treated_as_people():
    """`Requirements:`, `Impact:`, `Auto-summary (auto-generated):` must not
    become entities — they poison entity resolution downstream."""
    for source in ("slack", "jira", "fireflies", "hubspot"):
        for r in _parse_all(source):
            for ref in r["author_refs"] + r["mention_refs"]:
                assert ref.lower() not in NOT_A_SPEAKER, (
                    f"{source}: prose label {ref!r} leaked in as a person"
                )


def test_cross_source_join_keys_are_captured():
    """Ticket/PR/Fireflies ids are the deterministic multi-hop edges (§7.4)."""
    hits = {
        source: sum(
            1 for r in _parse_all(source) if r["raw_metadata"].get("cross_refs")
        )
        for source in ("github", "linear", "jira")
    }
    for source, n in hits.items():
        assert n >= 10, f"{source}: only {n}/20 docs yielded cross-source refs"


def test_surface_forms_are_preserved_verbatim():
    """Normalization must not resolve or canonicalise anything — Track B's
    entity resolution needs the raw variants as evidence."""
    refs = {ref for r in _parse_all("slack") for ref in r["author_refs"]}
    # Real fixture data contains both bare handles and Name-with-initial forms.
    assert any(" " in ref for ref in refs) or any("-" in ref for ref in refs)
