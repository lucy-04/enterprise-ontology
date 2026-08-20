"""A3 regression tests — Layer 1 keyword + vector search and fusion.

Query building and rank fusion are pure functions and are tested directly,
because they are where retrieval quality silently degrades: a change that makes
`fts_query` emit AND instead of OR, or that breaks RRF's rank ordering, still
returns plausible-looking results and only shows up as a worse score much later.

Index-backed tests build a small real index in a temp directory rather than
mocking SQLite, so FTS5's actual tokenizer and bm25 ranking are exercised.
Vector tests skip when the embedding model is not available offline.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.index.build import FTS_SCHEMA, SCHEMA
from src.index.search import SearchIndex, _snippet, fts_query, rrf


# ---------------------------------------------------------------------------
# query construction
# ---------------------------------------------------------------------------
def test_terms_are_or_ed_not_and_ed():
    """A question phrased differently from the document returns nothing under
    AND. bm25 already ranks documents matching more terms higher."""
    query = fts_query("who owns the billing pipeline")
    assert " OR " in query
    assert " AND " not in query


def test_stopwords_are_dropped():
    query = fts_query("what is the status of the deploy")
    assert "status" in query and "deploy" in query
    assert '"the"' not in query and '"is"' not in query


def test_identifiers_survive_intact():
    """Ticket ids and filenames are the cases keyword search wins on, so the
    tokenizer must not split or drop them."""
    assert '"eng-30521"' in fts_query("what happened with ENG-30521")
    assert '"config.yaml"' in fts_query("who changed config.yaml")


def test_every_term_is_quoted_so_punctuation_is_not_syntax():
    """Unquoted, a leading '-' reads as NOT and silently inverts the query."""
    for term in fts_query("ENG-1 OR NOT AND *").split(" OR "):
        assert term.startswith('"') and term.endswith('"')


def test_a_query_of_only_stopwords_still_returns_something():
    assert fts_query("what is the")


def test_empty_query_is_empty_not_a_crash():
    assert fts_query("") == ""
    assert fts_query("!!!") == ""


# ---------------------------------------------------------------------------
# fusion
# ---------------------------------------------------------------------------
def test_rrf_rewards_agreement_between_the_two_systems():
    """The point of fusing: a document *both* systems rank well beats one that
    only a single system puts first.

    "agreement" means appearing in both rankings — not being higher on average.
    Summing 1/(k+rank) is slightly convex, so rank 1 + rank 3 marginally
    outscores rank 2 + rank 2; that is expected and is not what this guards.
    """
    scores = rrf([["keyword_only", "agreed"], ["vector_only", "agreed"]])
    assert scores["agreed"] > scores["keyword_only"]
    assert scores["agreed"] > scores["vector_only"]


def test_rrf_ranks_by_position_not_by_score_scale():
    """bm25 and cosine are not comparable scales; only ranks are used."""
    scores = rrf([["first", "second", "third"]])
    assert scores["first"] > scores["second"] > scores["third"]


def test_rrf_handles_one_empty_ranking():
    assert rrf([["a", "b"], []])["a"] > 0


def test_rrf_k_damps_the_top_rank_advantage():
    """A large k flattens the curve, so one system cannot dominate the fusion."""
    sharp = rrf([["a", "b"]], k=1)
    flat = rrf([["a", "b"]], k=1000)
    assert (sharp["a"] - sharp["b"]) > (flat["a"] - flat["b"])


# ---------------------------------------------------------------------------
# index-backed
# ---------------------------------------------------------------------------
DOCS = [
    ("dsid_1", "jira", "ENG-30521 checkout latency regression",
     "The checkout service p99 latency regressed after the caching change."),
    ("dsid_2", "slack", "eng-runtime standup",
     "maria: the billing pipeline is owned by the payments team now."),
    ("dsid_3", "confluence", "Billing ownership",
     "Ownership of the billing pipeline transferred to Payments in March."),
    ("dsid_4", "gmail", "Vacation policy",
     "Unrelated content about time off and the holiday schedule."),
]


@pytest.fixture
def index(tmp_path):
    conn = sqlite3.connect(tmp_path / "docs.sqlite")
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO docs (doc_id, source_type, title, body, ts, thread_id, path, "
        "author_refs, mention_refs, raw_metadata) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(d, s, t, b, 1700000000, None, "", '["maria"]', "[]", '{"k": "v"}')
         for d, s, t, b in DOCS])
    conn.executescript(FTS_SCHEMA)
    conn.execute("INSERT INTO docs_fts(docs_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    idx = SearchIndex(tmp_path)
    yield idx
    idx.close()


def test_keyword_search_finds_the_right_document(index):
    hits = index.keyword("billing pipeline ownership", 5)
    assert hits and hits[0][0] in {"dsid_2", "dsid_3"}


def test_keyword_search_finds_an_exact_identifier(index):
    """The case vector search is bad at — ids carry little semantic signal."""
    assert index.keyword("ENG-30521", 5)[0][0] == "dsid_1"


def test_source_filter_restricts_results(index):
    hits = index.keyword("billing pipeline", 5, sources=["confluence"])
    assert hits and all(d == "dsid_3" for d, _ in hits)


def test_search_falls_back_to_keyword_when_no_vectors(index):
    """A keyword-only index must still serve search() — the vector build is a
    separate, slower stage and the system has to work in between."""
    assert index.vectors is None
    hits = index.search("billing pipeline ownership", k=3)
    assert hits and hits[0].doc_id in {"dsid_2", "dsid_3"}


def test_search_returns_populated_dochits(index):
    hit = index.search("checkout latency", k=1)[0]
    assert hit.doc_id and hit.source_type == "jira" and hit.title
    assert hit.snippet and hit.score > 0


def test_snippet_shows_the_matching_text_not_just_the_head(index):
    hit = index.search("holiday schedule", k=1)[0]
    assert "holiday" in hit.snippet.lower()


def test_get_docs_preserves_requested_order(index):
    docs = index.get_docs(["dsid_3", "dsid_1"])
    assert [d.doc_id for d in docs] == ["dsid_3", "dsid_1"]


def test_get_docs_rehydrates_list_and_dict_columns(index):
    """author_refs/mention_refs/raw_metadata are JSON in SQLite and must come
    back as the types the NormalizedDoc contract promises."""
    doc = index.get_docs(["dsid_1"])[0]
    assert doc.author_refs == ["maria"]
    assert doc.raw_metadata == {"k": "v"}
    assert doc.timestamp is not None


def test_get_docs_skips_unknown_ids_without_failing(index):
    assert [d.doc_id for d in index.get_docs(["nope", "dsid_1"])] == ["dsid_1"]


def test_empty_query_returns_nothing(index):
    assert index.search("", k=5) == []
    assert index.search("   ", k=5) == []


def test_missing_index_raises_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="just index"):
        SearchIndex(tmp_path / "nothing").search("anything")


# ---------------------------------------------------------------------------
# snippets — the evidence the answer is written from, not UI garnish
# ---------------------------------------------------------------------------
def test_a_short_document_passes_through_whole():
    """Windowing a document that already fits is pure loss. Measured against the
    benchmark's answer_facts, a windowed extract exposed 24% of the facts needed
    to answer while the full body exposed 67% — and the system abstained on
    questions whose gold document it had retrieved."""
    body = "Ownership moved to Priya in March. The rollback threshold is 4%."
    assert _snippet(body, "threshold", width=4000) == body


def test_a_long_document_is_windowed_around_several_query_terms():
    """A document matching three terms in three places was previously shown only
    around the first, so the halves of a multi-part question could never both
    appear in the evidence."""
    body = ("alpha " + "x" * 2000 + " bravo " + "y" * 2000 + " charlie")
    out = _snippet(body, "alpha bravo charlie", width=1200)
    assert "alpha" in out and "bravo" in out and "charlie" in out


def test_windows_are_marked_as_discontinuous():
    """The model must be able to tell that two windows are not adjacent text,
    or it will read across the gap and invent a connection."""
    body = ("alpha " + "x" * 3000 + " omega")
    out = _snippet(body, "alpha omega", width=900)
    assert "..." in out


def test_overlapping_windows_are_merged_rather_than_repeated():
    body = "The quota and the threshold are set together. " + "z" * 3000
    out = _snippet(body, "quota threshold", width=900)
    assert out.count("threshold are set") == 1


def test_a_query_with_no_usable_terms_still_returns_the_head_of_the_document():
    body = "y" * 5000
    out = _snippet(body, "of the a", width=900)
    assert out and len(out) <= 900


def test_snippet_never_returns_raw_newlines():
    """Newlines inside an evidence block make the context ambiguous about where
    one document ends and the next begins."""
    assert "\n" not in _snippet("line one\nline two\nline three", "two", width=4000)
