"""A8 — run the benchmark questions through the agent.  `just eval`

Reads `questions.jsonl`, calls Track B's router once per question (the single
A -> B call surface, CLAUDE.md §14.3), and writes the two files the benchmark
and the UI need:

    answer_evaluation/answers.jsonl   {question_id, answer, document_ids}
    answer_evaluation/traces.jsonl    route, evidence, grade decision, retries

Both are appended as questions finish, so an interrupted run keeps everything it
already earned. Re-running skips question ids already present — resume is the
default, not a flag.

**The quota guard.** Track B's router catches its own errors and abstains rather
than crashing, which is right for a live demo and dangerous for a batch run: if
the free-tier LLM quota dies at question 120, the remaining 380 come back as
confident-looking abstentions and land in the results table as if they were
real. `progress/track-b.md` recorded exactly this failure mode on Aug 20
(429 RESOURCE_EXHAUSTED). So the runner watches for a run of abstentions that
made no LLM calls and stops, keeping the good answers and saying plainly where
it stopped. A short honest run beats a long fabricated one.

Usage:
    python -m src.eval.run                        # all 500, resumable
    python -m src.eval.run --stratified 100       # proportional sample, all types
    python -m src.eval.run --types info_not_found conflicting_info
    python -m src.eval.run --limit 20 --parallelism 2
    python -m src.eval.run --restart              # discard previous answers
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from src.common.config import data_dir
from src.common.schemas import AnswerResult
from src.eval.score import load_jsonl, render, score

OUT_DIR = Path("answer_evaluation")


def answers_path() -> Path:
    return OUT_DIR / "answers.jsonl"


def traces_path() -> Path:
    return OUT_DIR / "traces.jsonl"


def _trace_json(result: AnswerResult) -> dict:
    """The trace, flattened. Paths become lengths and citations rather than full
    node/edge objects — this file is read for debugging and by the UI, and a
    full subgraph per question would make it unreadable at 500 lines."""
    trace = result.trace
    return {
        "question_id": result.question_id,
        "route": trace.route,
        "abstained": result.abstained,
        "confidence": result.confidence,
        "retrieved_doc_ids": list(trace.retrieved_doc_ids),
        "entity_ids": list(trace.entity_ids),
        "paths": [{"length": len(p.steps), "doc_ids": list(p.doc_ids)}
                  for p in trace.paths],
        "conflicts": len(trace.conflicts),
        "grade_passed": trace.grade_passed,
        "grade_reason": trace.grade_reason,
        "retries": trace.retries,
        "llm_calls": trace.llm_calls,
    }


def select(questions: list[dict], types: list[str] | None, limit: int | None,
           stratified: int | None) -> list[dict]:
    """Pick which questions to run.

    `--stratified N` keeps the real category mix rather than taking the first N,
    which would be almost entirely `basic` — the file is ordered by type, so a
    naive head() measures one category and calls it a score.
    """
    rows = questions
    if types:
        wanted = set(types)
        rows = [q for q in rows if q.get("question_type") in wanted]

    if stratified:
        by_type: dict[str, list[dict]] = defaultdict(list)
        for q in rows:
            by_type[q.get("question_type") or "unknown"].append(q)
        total = len(rows)
        picked: list[dict] = []
        for group in by_type.values():
            # At least one of every type, so no category is missing from the table.
            take = max(1, round(stratified * len(group) / total))
            picked += group[:take]
        rows = picked

    if limit:
        rows = rows[:limit]
    return rows


# The router records a caught exception as "router error, abstained: <repr>"
# (src/agent/router.py). That prefix is the only reliable way to tell a failure
# from a decision.
_ROUTER_ERROR = "router error"


def is_failed_answer(abstained: bool, llm_calls: int, grade_reason: str) -> bool:
    """True when an abstention was a failure rather than a judgement.

    The tempting test — "abstained with zero LLM calls" — is wrong, and wrong in
    the direction that destroys real results. Every LLM call is disk-cached, and
    a cache hit deliberately does not count against `calls`, so a resumed run is
    full of perfectly good answers reporting `llm_calls == 0`. Measured on this
    corpus: 66 of 79 traces had zero calls and every single one carried a real
    grader verdict.

    So the signal is the router's own error marker, not the call count. An empty
    reason with no calls also counts, since that means nothing ran at all.
    """
    reason = (grade_reason or "").strip()
    if not abstained:
        return False
    if reason.startswith(_ROUTER_ERROR):
        return True
    return not reason and not llm_calls


class QuotaGuard:
    """Trip when the model has plainly stopped responding.

    A healthy abstention is a decision: the grader ran, judged the evidence
    insufficient, and declined. A starved one is a failure wearing the same
    clothes. Only the second kind counts here, so a cautious — or merely
    cache-warm — run never trips the guard.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.streak = 0
        self.tripped = False
        self._lock = threading.Lock()

    def observe(self, result: AnswerResult) -> None:
        with self._lock:
            failed = is_failed_answer(result.abstained, result.trace.llm_calls,
                                      result.trace.grade_reason)
            self.streak = self.streak + 1 if failed else 0
            if self.limit and self.streak >= self.limit:
                self.tripped = True


def run(questions: list[dict], parallelism: int, guard: QuotaGuard) -> int:
    """Answer each question, appending results as they finish."""
    from src.agent.router import answer
    from src.graph.client import GraphClient

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = GraphClient()
    write_lock = threading.Lock()
    done = 0

    def one(question: dict) -> AnswerResult:
        return answer(question["question"], client, question["question_id"])

    with (open(answers_path(), "a") as af,
          open(traces_path(), "a") as tf,
          ThreadPoolExecutor(max_workers=parallelism) as pool):
        futures = {pool.submit(one, q): q for q in questions}
        try:
            with tqdm(total=len(questions), desc="  answering", unit="q") as bar:
                for future in as_completed(futures):
                    question = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:  # never lose the rest of the run
                        result = AnswerResult(
                            question_id=question["question_id"],
                            answer="", abstained=True, confidence=0.0)
                        result.trace.grade_reason = f"runner error: {exc!r}"

                    with write_lock:
                        af.write(json.dumps(result.to_answer_jsonl()) + "\n")
                        tf.write(json.dumps(_trace_json(result)) + "\n")
                        af.flush()
                        tf.flush()
                    done += 1
                    guard.observe(result)
                    bar.update(1)
                    if guard.tripped:
                        bar.write(
                            f"\n  STOPPING: {guard.streak} consecutive abstentions "
                            f"with no LLM calls — the model is not responding "
                            f"(quota exhausted?). {done} answers kept.")
                        for pending in futures:
                            pending.cancel()
                        break
        finally:
            client.close()
    return done


def _drop_starved() -> int:
    """Remove answers produced while the model was unreachable, so a resume
    retries them instead of banking them.

    A failed answer is not a decision, it is an error that happens to look like
    one — quota exhaustion, a dropped connection, a DNS blip. Resume skips any
    question id already present in answers.jsonl, so without this a single
    network wobble is silently frozen into the final results table as a run of
    confident-looking refusals. (Measured 2026-08-20: a DNS failure produced 52
    of them in one run.) See is_failed_answer for what counts — notably NOT the
    call count, which is zero for every cache hit.
    """
    answers = load_jsonl(answers_path())
    traces = load_jsonl(traces_path())
    if not answers or not traces:
        return 0

    bad = {t["question_id"] for t in traces
           if is_failed_answer(bool(t.get("abstained")), int(t.get("llm_calls") or 0),
                               t.get("grade_reason") or "")}
    if not bad:
        return 0

    keep_a = [a for a in answers if a["question_id"] not in bad]
    keep_t = [t for t in traces if t["question_id"] not in bad]
    answers_path().write_text("".join(json.dumps(a) + "\n" for a in keep_a))
    traces_path().write_text("".join(json.dumps(t) + "\n" for t in keep_t))
    return len(answers) - len(keep_a)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--questions", type=Path, default=data_dir() / "raw" / "questions.jsonl")
    ap.add_argument("--types", nargs="*", default=None, help="only these question types")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--stratified", type=int, default=None,
                    help="sample N questions keeping the real category mix")
    ap.add_argument("--parallelism", type=int, default=4)
    ap.add_argument("--restart", action="store_true",
                    help="discard previous answers instead of resuming")
    ap.add_argument("--max-starved", type=int, default=25,
                    help="stop after this many LLM-less abstentions in a row (0 = never)")
    ap.add_argument("--score-only", action="store_true",
                    help="skip the run, just score what is already on disk")
    args = ap.parse_args(argv)

    questions = load_jsonl(args.questions)
    if not questions:
        print(f"no questions at {args.questions} — run `just fetch-data`", file=sys.stderr)
        return 1

    if args.restart and not args.score_only:
        answers_path().unlink(missing_ok=True)
        traces_path().unlink(missing_ok=True)

    if not args.score_only:
        wanted = select(questions, args.types, args.limit, args.stratified)
        starved = _drop_starved()
        already = {a["question_id"] for a in load_jsonl(answers_path())}
        todo = [q for q in wanted if q["question_id"] not in already]

        if starved:
            print(f"discarded {starved} answer(s) produced while the model was "
                  f"unreachable — they will be retried")
        print(f"{len(wanted)} selected, {len(wanted) - len(todo)} already answered, "
              f"{len(todo)} to run (parallelism {args.parallelism})")
        if todo:
            guard = QuotaGuard(args.max_starved)
            t0 = time.time()
            done = run(todo, args.parallelism, guard)
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed else 0
            print(f"  {done} answered in {elapsed:.0f}s ({rate:.1f}/s)\n")

    answers = load_jsonl(answers_path())
    if not answers:
        print("no answers to score", file=sys.stderr)
        return 1

    # Score only what was actually attempted, so a partial run reports honest
    # per-category numbers instead of counting unrun questions as failures.
    attempted = {a["question_id"] for a in answers}
    print(render(score([q for q in questions if q["question_id"] in attempted], answers, load_jsonl(traces_path()))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
