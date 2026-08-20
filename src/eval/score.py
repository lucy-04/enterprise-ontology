"""A8 — score answers.jsonl against the benchmark's own gold data. No LLM.

`questions.jsonl` ships `expected_doc_ids` for 470 of the 500 questions, so two
of the benchmark's four metrics — Document Recall and Invalid Extra Documents —
are exact set arithmetic. They cost nothing and cannot fail.

That separation is deliberate. The official scorer is an LLM judge, and free
tiers run out (`progress/track-b.md` recorded a 429 mid-session on Aug 20). If
answer grading is the only way we can produce numbers, a dead quota means no
results table at all. Scoring what is deterministic first means the README
always has a real, honest table, and the LLM judge only adds Correctness and
Completeness on top.

Everything is reported per question type. The aggregate hides the signal: a
`basic` lookup and a `high_level` synthesis question fail for unrelated reasons
and are fixed in unrelated ways.

    document recall   per question, |cited ∩ gold| / |gold|, then averaged.
                      The benchmark's headline retrieval metric.
    extra documents   per question, |cited \\ gold|. The benchmark explicitly
                      penalises padding the citation list, so this is a cost,
                      not a neutral count.
    precision         |cited ∩ gold| / |cited|. Reported because recall alone
                      is trivially gamed by citing everything.
    abstention        of the 20 `info_not_found` questions, how many were
                      correctly declined.
    false confidence  answered confidently when there was nothing to find.
                      Track B's headline safety metric (CLAUDE.md §11, B5).

The 10 `high_level` questions carry no gold documents by design — there is no
single ground-truth document for them — so recall is undefined and reported as
"n/a" rather than as zero. Scoring them 0 would understate the system by 2% for
a property the benchmark never asserted.

Usage:
    python -m src.eval.score                          # table to stdout
    python -m src.eval.score --markdown > results.md  # for the README
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src.common.config import data_dir

# Question types that ship no gold documents, and why. Kept explicit so the
# scorer never silently treats "no gold" as "scored zero".
NO_GOLD_BY_DESIGN = {
    "info_not_found": "nothing to find — the correct behaviour is to abstain",
    "high_level": "no single ground-truth document exists",
}

TYPE_ORDER = (
    "basic", "semantic", "intra_document_reasoning", "project_related",
    "constrained", "conflicting_info", "completeness", "miscellaneous",
    "high_level", "info_not_found",
)


@dataclass
class TypeScore:
    """Scores for one question type."""

    question_type: str
    n: int = 0
    recalls: list[float] = field(default_factory=list)
    precisions: list[float] = field(default_factory=list)
    extras: list[int] = field(default_factory=list)
    abstained: int = 0
    answered: int = 0
    unanswered: int = 0          # in questions.jsonl but absent from answers.jsonl

    @property
    def has_gold(self) -> bool:
        return self.question_type not in NO_GOLD_BY_DESIGN

    @property
    def recall(self) -> float | None:
        return sum(self.recalls) / len(self.recalls) if self.recalls else None

    @property
    def precision(self) -> float | None:
        return sum(self.precisions) / len(self.precisions) if self.precisions else None

    @property
    def extra(self) -> float | None:
        return sum(self.extras) / len(self.extras) if self.extras else None

    @property
    def abstention_rate(self) -> float | None:
        return self.abstained / self.n if self.n else None


# Phrases an abstention uses. Only consulted when no trace is available, and
# only for answers that cited nothing — a real answer always carries citations.
_DECLINES = ("don't have enough information", "do not have enough information",
             "not in the data", "cannot answer", "can't answer", "no information")


def _declines(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    low = stripped.lower()
    return any(phrase in low for phrase in _DECLINES)


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def score(questions: list[dict], answers: list[dict],
          traces: list[dict] | None = None) -> dict:
    """Score answers against questions. Pure — no I/O, no LLM.

    `traces` is optional but strongly preferred: the benchmark's answer format
    is only {question_id, answer, document_ids}, so answers.jsonl has nowhere to
    record that a refusal was deliberate. Without the trace, a polite abstention
    ("I don't have enough information to answer that.") is indistinguishable
    from a confident wrong answer, and the safety metrics invert.
    """
    by_id = {a["question_id"]: a for a in answers}
    abstained_by_id = {t["question_id"]: bool(t.get("abstained"))
                       for t in (traces or [])}
    scores: dict[str, TypeScore] = {}

    for question in questions:
        qtype = question.get("question_type") or "unknown"
        bucket = scores.setdefault(qtype, TypeScore(question_type=qtype))
        bucket.n += 1

        given = by_id.get(question["question_id"])
        if given is None:
            bucket.unanswered += 1
            continue

        # The trace is authoritative. Falling back to the answer payload only
        # catches the flag if Track B ever adds one, plus the shape every
        # abstention has regardless of wording: no citations, and either no text
        # or text that declines.
        if question["question_id"] in abstained_by_id:
            abstained = abstained_by_id[question["question_id"]]
        else:
            abstained = bool(given.get("abstained")) or (
                not given.get("document_ids")
                and _declines(given.get("answer") or ""))
        if abstained:
            bucket.abstained += 1
        else:
            bucket.answered += 1

        gold = set(question.get("expected_doc_ids") or [])
        cited = set(given.get("document_ids") or [])

        if gold:
            bucket.recalls.append(len(cited & gold) / len(gold))
            bucket.extras.append(len(cited - gold))
            if cited:
                bucket.precisions.append(len(cited & gold) / len(cited))
            else:
                bucket.precisions.append(0.0)
        elif qtype not in NO_GOLD_BY_DESIGN:
            # A gold list we expected and did not get — record it rather than
            # dropping the question silently out of the denominator.
            bucket.recalls.append(0.0)

    return {"by_type": scores, "totals": _totals(scores)}


def _totals(scores: dict[str, TypeScore]) -> dict:
    """Corpus-wide rollups. Gold-bearing questions only for recall/precision."""
    gold_types = [s for s in scores.values() if s.has_gold]
    recalls = [r for s in gold_types for r in s.recalls]
    precisions = [p for s in gold_types for p in s.precisions]
    extras = [e for s in gold_types for e in s.extras]

    notfound = scores.get("info_not_found")
    return {
        "questions": sum(s.n for s in scores.values()),
        "answered": sum(s.answered for s in scores.values()),
        "abstained": sum(s.abstained for s in scores.values()),
        "unanswered": sum(s.unanswered for s in scores.values()),
        "document_recall": sum(recalls) / len(recalls) if recalls else None,
        "precision": sum(precisions) / len(precisions) if precisions else None,
        "extra_documents": sum(extras) / len(extras) if extras else None,
        # The safety number: of the questions with nothing to find, how many did
        # we answer anyway? Zero is the target.
        "false_confidence": (notfound.answered / notfound.n
                             if notfound and notfound.n else None),
        "abstention_accuracy": (notfound.abstained / notfound.n
                                if notfound and notfound.n else None),
        # The cost of that caution: abstentions on questions that DID have an
        # answer. Reported alongside, because either number alone is misleading.
        "false_abstention": _false_abstention(gold_types),
    }


def _false_abstention(gold_types: list[TypeScore]) -> float | None:
    n = sum(s.n for s in gold_types)
    return sum(s.abstained for s in gold_types) / n if n else None


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def render(result: dict, markdown: bool = False) -> str:
    """Per-category table plus the rollup. Markdown mode is README-ready."""
    scores = result["by_type"]
    totals = result["totals"]
    ordered = [scores[t] for t in TYPE_ORDER if t in scores]
    ordered += [s for t, s in scores.items() if t not in TYPE_ORDER]

    rows = [("Question type", "n", "Doc recall", "Precision", "Extra docs",
             "Abstained")]
    for s in ordered:
        rows.append((
            s.question_type.replace("_", " "),
            str(s.n),
            "n/a — by design" if not s.has_gold else _pct(s.recall),
            "n/a" if not s.has_gold else _pct(s.precision),
            "n/a" if not s.has_gold else _num(s.extra),
            _pct(s.abstention_rate),
        ))

    out: list[str] = []
    if markdown:
        widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
        def line(cells):
            return "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells)) + " |"
        out.append(line(rows[0]))
        out.append("|" + "|".join("-" * (w + 2) for w in widths) + "|")
        out += [line(r) for r in rows[1:]]
    else:
        widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
        for i, row in enumerate(rows):
            out.append("  ".join(c.ljust(widths[j]) for j, c in enumerate(row)))
            if i == 0:
                out.append("  ".join("-" * w for w in widths))

    out.append("")
    out.append(f"**{totals['questions']} questions** — "
               f"{totals['answered']} answered, {totals['abstained']} abstained"
               + (f", {totals['unanswered']} not run" if totals["unanswered"] else ""))
    out.append("")
    out.append(f"- Document recall: **{_pct(totals['document_recall'])}**")
    out.append(f"- Citation precision: **{_pct(totals['precision'])}**")
    out.append(f"- Invalid extra documents per question: **{_num(totals['extra_documents'])}**")
    out.append(f"- Abstention accuracy (`info_not_found`): "
               f"**{_pct(totals['abstention_accuracy'])}**")
    out.append(f"- False confidence (answered when nothing to find): "
               f"**{_pct(totals['false_confidence'])}**")
    out.append(f"- False abstention (declined when an answer existed): "
               f"**{_pct(totals['false_abstention'])}**")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--answers", type=Path,
                    default=Path("answer_evaluation/answers.jsonl"))
    ap.add_argument("--questions", type=Path, default=data_dir() / "raw" / "questions.jsonl")
    ap.add_argument("--markdown", action="store_true", help="README-ready table")
    ap.add_argument("--json", type=Path, default=None, help="also write raw scores here")
    ap.add_argument("--include-unrun", action="store_true",
                    help="count questions that were never attempted as failures")
    args = ap.parse_args(argv)

    questions = load_jsonl(args.questions)
    if not questions:
        print(f"no questions at {args.questions}", file=sys.stderr)
        return 1
    answers = load_jsonl(args.answers)
    if not answers:
        print(f"no answers at {args.answers} — run `just eval` first", file=sys.stderr)
        return 1

    traces = load_jsonl(args.answers.parent / "traces.jsonl")
    if not args.include_unrun:
        # A partial run must report the questions it actually attempted. Leaving
        # 485 unrun questions in the denominator turns every rate into a
        # statement about how far the run got, not about how well it did.
        attempted = {a["question_id"] for a in answers}
        questions = [q for q in questions if q["question_id"] in attempted]
    result = score(questions, answers, traces)
    print(render(result, markdown=args.markdown))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "totals": result["totals"],
            "by_type": {t: {"n": s.n, "recall": s.recall, "precision": s.precision,
                            "extra": s.extra, "abstained": s.abstained,
                            "answered": s.answered, "unanswered": s.unanswered}
                        for t, s in result["by_type"].items()},
        }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
