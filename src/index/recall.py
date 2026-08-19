"""A3 — measure Layer 1 retrieval against the benchmark's own gold documents.

`questions.jsonl` ships `expected_doc_ids` for 470 of the 500 questions, so
retrieval quality can be measured exactly, offline, for free. That matters:
the official scorer is an LLM judge that burns free-tier quota on every run
(`progress/track-b.md` → LLM budget), so it should be spent on answer quality,
not on catching a broken index.

Reported per question type, because the aggregate hides the useful signal —
`basic` questions and `high_level` questions fail for completely different
reasons and are fixed in completely different ways.

    recall@k   fraction of questions where at least one gold document is in
               the top k. If this is low, no amount of answer synthesis helps.
    hit rate   fraction of ALL gold documents retrieved — the metric that
               matters for `completeness` questions, which need every one.

Usage:
    python -m src.index.recall                 # k=20, all questions
    python -m src.index.recall --k 5 --k 20 --k 50
    python -m src.index.recall --mode keyword  # isolate one retriever
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from src.common.config import data_dir
from src.index.search import SearchIndex


def load_questions(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def indexed_doc_ids(index: SearchIndex) -> set[str]:
    return {row[0] for row in index.conn.execute("SELECT doc_id FROM docs")}


def evaluate(index: SearchIndex, questions: list[dict], ks: list[int],
             mode: str = "hybrid", use_sources: bool = False) -> dict:
    by_type: dict[str, list[dict]] = defaultdict(list)
    present = indexed_doc_ids(index)

    for question in questions:
        gold = set(question.get("expected_doc_ids") or [])
        if not gold:
            continue  # info_not_found questions have no gold document by design
        sources = question.get("source_types") if use_sources else None

        # Each k is a separate query rather than one query at max(k) sliced
        # down. Hybrid search sizes its candidate pool from k, so slicing a
        # larger run measures a configuration the system never actually uses.
        ranked_at: dict[int, list[str]] = {}
        for k in ks:
            if mode == "keyword":
                ranked_at[k] = [d for d, _ in index.keyword(question["question"], k, sources)]
            elif mode == "vector":
                ranked_at[k] = [d for d, _ in index.vector(question["question"], k, sources)]
            else:
                ranked_at[k] = [h.doc_id
                                for h in index.search(question["question"], k, sources)]

        by_type[question.get("question_type") or "unknown"].append({
            "hit": {k: bool(gold & set(ranked_at[k])) for k in ks},
            "found": {k: len(gold & set(ranked_at[k])) for k in ks},
            "gold": len(gold),
            # Whether the question is answerable from the corpus we actually
            # hold. While the corpus is a subset, raw recall is bounded by this
            # and reads as a retrieval failure when it is a coverage gap.
            "reachable": bool(gold & present),
            "gold_present": len(gold & present),
        })

    def summarise(rows: list[dict]) -> dict:
        n = len(rows)
        total_gold = sum(r["gold"] for r in rows)
        reachable = [r for r in rows if r["reachable"]]
        return {
            "n": n,
            "reachable": len(reachable),
            "ceiling": len(reachable) / n if n else 0.0,
            "recall": {k: sum(r["hit"][k] for r in rows) / n for k in ks},
            # Recall among questions whose gold document is actually indexed —
            # this is the number that measures the retriever rather than the
            # download, and the one to watch while iterating.
            "recall_reachable": {
                k: (sum(r["hit"][k] for r in reachable) / len(reachable))
                if reachable else 0.0 for k in ks},
            "hit_rate": {k: (sum(r["found"][k] for r in rows) / total_gold
                             if total_gold else 0.0) for k in ks},
        }

    result = {qtype: summarise(rows) for qtype, rows in sorted(by_type.items())}
    everything = [r for rows in by_type.values() for r in rows]
    result["OVERALL"] = summarise(everything)
    return result


def render(result: dict, ks: list[int], mode: str) -> str:
    def row(name: str, stats: dict) -> str:
        return (f"{name:<26}{stats['n']:>5}{stats['ceiling']:>8.3f}  "
                + "  ".join(f"{stats['recall'][k]:.3f}" for k in ks)
                + "   " + "  ".join(f"{stats['recall_reachable'][k]:.3f}" for k in ks))

    head = (f"{'question type':<26}{'n':>5}{'ceiling':>8}  "
            + "  ".join(f"r@{k:<4}" for k in ks)
            + "   " + "  ".join(f"cr@{k:<3}" for k in ks))
    lines = [f"\nLayer 1 retrieval — {mode}",
             "  r@k  = recall over all questions (bounded by ceiling)",
             "  cr@k = recall over questions whose gold document is indexed",
             "         — this is the retriever's own score",
             "", head, "-" * len(head)]
    for qtype, stats in result.items():
        if qtype != "OVERALL":
            lines.append(row(qtype, stats))
    lines.append("-" * len(head))
    lines.append(row("OVERALL", result["OVERALL"]))
    ceiling = result["OVERALL"]["ceiling"]
    if ceiling < 0.99:
        lines.append(
            f"\nNOTE: only {ceiling:.1%} of questions have a gold document in this "
            f"index.\n      r@k cannot exceed that. Judge the retriever by cr@k "
            f"until the\n      full corpus is loaded.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--questions", default=str(data_dir() / "raw" / "questions.jsonl"))
    ap.add_argument("--k", type=int, action="append", default=None)
    ap.add_argument("--mode", choices=["hybrid", "keyword", "vector"], default="hybrid")
    ap.add_argument("--limit", type=int, default=None, help="first N questions (dev)")
    ap.add_argument("--use-sources", action="store_true",
                    help="restrict each query to the question's own source_types "
                         "(an upper bound — the real system does not know them)")
    ap.add_argument("--out", default=None, help="write JSON results here")
    args = ap.parse_args(argv)

    ks = sorted(args.k or [5, 20, 50])
    path = Path(args.questions)
    if not path.exists():
        print(f"no questions file at {path} — run `just fetch-data`")
        return 1

    questions = load_questions(path)
    if args.limit:
        questions = questions[: args.limit]

    index = SearchIndex()
    try:
        print(f"index holds {index.count():,} documents")
        if index.vectors is None and args.mode != "keyword":
            print("warning: no vector index — run `just index` "
                  "(results below are keyword-only)")
        result = evaluate(index, questions, ks, args.mode, args.use_sources)
    finally:
        index.close()

    print(render(result, ks, args.mode))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2))
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
