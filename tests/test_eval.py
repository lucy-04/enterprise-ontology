"""A8 regression tests — the offline scorer and the run selection logic.

Everything here is pure: no LLM, no HydraDB, no search index. The point of the
deterministic scorer is that it always works, so its tests must always run.
"""

from __future__ import annotations

from src.common.schemas import AnswerResult
from src.eval.run import QuotaGuard, _trace_json, select
from src.eval.score import score


def q(qid, qtype, gold=()):
    return {"question_id": qid, "question_type": qtype,
            "question": "?", "expected_doc_ids": list(gold)}


def a(qid, docs=(), answer="something", abstained=False):
    return {"question_id": qid, "answer": answer,
            "document_ids": list(docs), "abstained": abstained}


# ---------------------------------------------------------------------------
# document recall / precision / extra documents
# ---------------------------------------------------------------------------
def test_perfect_citation_scores_full_recall_and_no_extras():
    result = score([q("q1", "basic", ["d1"])], [a("q1", ["d1"])])
    basic = result["by_type"]["basic"]
    assert basic.recall == 1.0 and basic.precision == 1.0 and basic.extra == 0


def test_padding_the_citation_list_is_penalised_not_rewarded():
    """Citing everything would ace recall, so precision and the extra-document
    count are what stop that being a winning strategy — the benchmark penalises
    invalid extra documents explicitly (CLAUDE.md §3.1)."""
    result = score([q("q1", "basic", ["d1"])], [a("q1", ["d1", "x", "y", "z"])])
    basic = result["by_type"]["basic"]
    assert basic.recall == 1.0          # found it
    assert basic.extra == 3             # and dragged in three that don't belong
    assert basic.precision == 0.25


def test_partial_recall_on_a_multi_document_question():
    result = score([q("q1", "completeness", ["d1", "d2", "d3", "d4"])],
                   [a("q1", ["d1", "d2"])])
    assert result["by_type"]["completeness"].recall == 0.5


def test_citing_nothing_scores_zero_precision_rather_than_dividing_by_zero():
    result = score([q("q1", "basic", ["d1"])], [a("q1", [])])
    assert result["by_type"]["basic"].precision == 0.0


# ---------------------------------------------------------------------------
# the two types that carry no gold documents
# ---------------------------------------------------------------------------
def test_high_level_recall_is_undefined_not_zero():
    """`high_level` questions ship no gold documents because no single document
    answers them. Scoring them 0 would understate the system for a property the
    benchmark never asserted."""
    result = score([q("q1", "high_level")], [a("q1", ["d9"])])
    high = result["by_type"]["high_level"]
    assert high.has_gold is False
    assert high.recall is None


def test_abstaining_on_info_not_found_is_the_correct_answer():
    questions = [q(f"q{i}", "info_not_found") for i in range(4)]
    answers = [a(f"q{i}", [], "I don't know.", abstained=True) for i in range(4)]
    totals = score(questions, answers)["totals"]
    assert totals["abstention_accuracy"] == 1.0
    assert totals["false_confidence"] == 0.0


def test_answering_an_unanswerable_question_is_counted_as_false_confidence():
    """The headline safety metric (CLAUDE.md §11, B5): confidently answering
    when there is nothing to find is the failure the abstention gate exists to
    prevent."""
    questions = [q("q1", "info_not_found"), q("q2", "info_not_found")]
    answers = [a("q1", ["d1"], "Definitely 42."), a("q2", [], "", abstained=True)]
    totals = score(questions, answers)["totals"]
    assert totals["false_confidence"] == 0.5
    assert totals["abstention_accuracy"] == 0.5


def test_empty_answer_with_no_citations_counts_as_an_abstention():
    """Track B may return an empty answer instead of setting the flag; both are
    a refusal to guess and must score the same."""
    result = score([q("q1", "info_not_found")], [a("q1", [], "", abstained=False)])
    assert result["by_type"]["info_not_found"].abstained == 1


def test_false_abstention_is_reported_alongside_abstention_accuracy():
    """Abstaining on everything would ace the safety metric, so the cost of that
    caution has to be visible in the same table."""
    questions = [q("q1", "basic", ["d1"]), q("q2", "info_not_found")]
    answers = [a("q1", [], "", abstained=True), a("q2", [], "", abstained=True)]
    totals = score(questions, answers)["totals"]
    assert totals["abstention_accuracy"] == 1.0   # looks perfect
    assert totals["false_abstention"] == 1.0      # and this is why it isn't


# ---------------------------------------------------------------------------
# partial runs
# ---------------------------------------------------------------------------
def test_a_question_that_was_never_run_is_not_scored_as_a_failure():
    result = score([q("q1", "basic", ["d1"]), q("q2", "basic", ["d2"])],
                   [a("q1", ["d1"])])
    basic = result["by_type"]["basic"]
    assert basic.unanswered == 1
    assert basic.recall == 1.0     # scored on the one that ran, not averaged with 0


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------
def test_stratified_sampling_keeps_every_category():
    """questions.jsonl is ordered by type, so taking the first N would sample
    almost nothing but `basic` and report it as an overall score."""
    questions = ([q(f"b{i}", "basic", ["d"]) for i in range(175)]
                 + [q(f"n{i}", "info_not_found") for i in range(20)]
                 + [q(f"h{i}", "high_level") for i in range(10)])
    picked = select(questions, None, None, stratified=50)
    types = {p["question_type"] for p in picked}
    assert types == {"basic", "info_not_found", "high_level"}
    assert len(picked) < len(questions)


def test_rare_categories_survive_a_small_sample():
    """Rounding a 10-of-500 category into a 20-question sample gives 0.4 — it
    must still appear, or the table silently loses a whole row."""
    questions = ([q(f"b{i}", "basic", ["d"]) for i in range(490)]
                 + [q(f"h{i}", "high_level") for i in range(10)])
    picked = select(questions, None, None, stratified=20)
    assert any(p["question_type"] == "high_level" for p in picked)


def test_type_filter_selects_only_that_type():
    questions = [q("q1", "basic", ["d"]), q("q2", "conflicting_info", ["d"])]
    picked = select(questions, ["conflicting_info"], None, None)
    assert [p["question_id"] for p in picked] == ["q2"]


# ---------------------------------------------------------------------------
# the quota guard
# ---------------------------------------------------------------------------
def _res(abstained: bool, llm_calls: int, reason: str = "NOT_SUPPORTED") -> AnswerResult:
    result = AnswerResult(question_id="q", answer="", abstained=abstained)
    result.trace.llm_calls = llm_calls
    result.trace.grade_reason = reason
    return result


def _err(exc: str = "ConnectError('dns')") -> AnswerResult:
    return _res(True, 0, f"router error, abstained: {exc}")


def test_guard_trips_on_a_run_of_router_errors():
    """Track B's router abstains rather than crashing when the LLM fails, so a
    dead connection looks exactly like a cautious system. The router's own error
    marker is what separates them."""
    guard = QuotaGuard(limit=3)
    for _ in range(3):
        guard.observe(_err())
    assert guard.tripped


def test_guard_ignores_abstentions_the_grader_actually_decided():
    """A real abstention reached a verdict. A run of them is the system working,
    not failing, and must not stop the run."""
    guard = QuotaGuard(limit=3)
    for _ in range(10):
        guard.observe(_res(abstained=True, llm_calls=2))
    assert not guard.tripped


def test_guard_does_not_trip_on_cache_hits():
    """THE regression that matters: every LLM call is disk-cached and a cache
    hit does not count against `calls`, so a resumed run is full of good answers
    reporting llm_calls == 0. Measured: 66 of 79 traces had zero calls and all
    66 carried a real verdict. Counting those as failures would abort a healthy
    run and delete real results."""
    guard = QuotaGuard(limit=3)
    for _ in range(20):
        guard.observe(_res(abstained=True, llm_calls=0, reason="NOT_SUPPORTED: ..."))
    assert not guard.tripped


def test_a_single_good_answer_resets_the_streak():
    guard = QuotaGuard(limit=3)
    guard.observe(_err())
    guard.observe(_err())
    guard.observe(_res(abstained=False, llm_calls=1))
    guard.observe(_err())
    assert not guard.tripped


def test_guard_can_be_disabled():
    guard = QuotaGuard(limit=0)
    for _ in range(100):
        guard.observe(_err())
    assert not guard.tripped


# ---------------------------------------------------------------------------
# trace
# ---------------------------------------------------------------------------
def test_trace_records_the_grade_decision_and_retry_count():
    result = AnswerResult(question_id="q1", answer="x", document_ids=["d1"])
    result.trace.route = "conflict"
    result.trace.grade_passed = False
    result.trace.grade_reason = "insufficient"
    result.trace.retries = 1
    result.trace.llm_calls = 3
    payload = _trace_json(result)
    assert payload["route"] == "conflict"
    assert payload["grade_passed"] is False
    assert payload["retries"] == 1 and payload["llm_calls"] == 3


# ---------------------------------------------------------------------------
# abstention detection — answers.jsonl cannot carry the flag
# ---------------------------------------------------------------------------
def test_trace_is_authoritative_for_abstention():
    """The benchmark's answer format is {question_id, answer, document_ids} —
    there is nowhere to record that a refusal was deliberate. Read the trace, or
    a polite abstention scores as a confident wrong answer and the safety
    metrics invert."""
    questions = [q("q1", "info_not_found")]
    answers = [a("q1", [], "I don't have enough information to answer that.")]
    traces = [{"question_id": "q1", "abstained": True}]
    totals = score(questions, answers, traces)["totals"]
    assert totals["abstention_accuracy"] == 1.0
    assert totals["false_confidence"] == 0.0


def test_without_a_trace_a_declining_answer_still_reads_as_an_abstention():
    questions = [q("q1", "info_not_found")]
    answers = [a("q1", [], "I don't have enough information to answer that.")]
    assert score(questions, answers)["by_type"]["info_not_found"].abstained == 1


def test_a_confident_answer_with_no_citations_is_not_an_abstention():
    """Declining is a specific act. An answer that asserts something without
    citing anything is a hallucination, and must not be laundered into caution."""
    questions = [q("q1", "info_not_found")]
    answers = [a("q1", [], "The total annual revenue is $42M.")]
    assert score(questions, answers)["totals"]["false_confidence"] == 1.0


# ---------------------------------------------------------------------------
# resume must not bank failures
# ---------------------------------------------------------------------------
def test_starved_answers_are_discarded_so_a_resume_retries_them(tmp_path, monkeypatch):
    """An abstention with no LLM call behind it is a failure wearing the shape of
    a decision. Resume skips any id already in answers.jsonl, so keeping these
    freezes one network wobble into the final table as confident refusals."""
    import json as _json

    from src.eval import run as runner

    out = tmp_path / "answer_evaluation"
    out.mkdir()
    monkeypatch.setattr(runner, "OUT_DIR", out)

    (out / "answers.jsonl").write_text(
        _json.dumps({"question_id": "good", "answer": "real", "document_ids": ["d1"]})
        + "\n"
        + _json.dumps({"question_id": "starved", "answer": "", "document_ids": []})
        + "\n")
    (out / "traces.jsonl").write_text(
        _json.dumps({"question_id": "good", "abstained": False, "llm_calls": 2,
                     "grade_reason": "SUPPORTED"}) + "\n"
        + _json.dumps({"question_id": "starved", "abstained": True, "llm_calls": 0,
                       "grade_reason": "router error, abstained: ConnectError()"})
        + "\n")

    assert runner._drop_starved() == 1
    kept = [_json.loads(x) for x in (out / "answers.jsonl").read_text().splitlines()]
    assert [k["question_id"] for k in kept] == ["good"]


def test_a_real_abstention_is_kept_on_resume(tmp_path, monkeypatch):
    """The grader burned a call to reach that verdict — it is a result."""
    import json as _json

    from src.eval import run as runner

    out = tmp_path / "answer_evaluation"
    out.mkdir()
    monkeypatch.setattr(runner, "OUT_DIR", out)
    (out / "answers.jsonl").write_text(
        _json.dumps({"question_id": "q", "answer": "I don't know.", "document_ids": []})
        + "\n")
    (out / "traces.jsonl").write_text(
        _json.dumps({"question_id": "q", "abstained": True, "llm_calls": 2,
                     "grade_reason": "NOT_SUPPORTED: nothing in the evidence"}) + "\n")

    assert runner._drop_starved() == 0


def test_a_cached_abstention_survives_a_resume(tmp_path, monkeypatch):
    """A cache hit reports llm_calls == 0 while being a perfectly good answer.
    Deleting those on resume would throw away real results every time."""
    import json as _json

    from src.eval import run as runner

    out = tmp_path / "answer_evaluation"
    out.mkdir()
    monkeypatch.setattr(runner, "OUT_DIR", out)
    (out / "answers.jsonl").write_text(
        _json.dumps({"question_id": "q", "answer": "I don't know.",
                     "document_ids": []}) + "\n")
    (out / "traces.jsonl").write_text(
        _json.dumps({"question_id": "q", "abstained": True, "llm_calls": 0,
                     "grade_reason": "NOT_SUPPORTED: the evidence does not say"})
        + "\n")

    assert runner._drop_starved() == 0
