"""Grading + answer synthesis for the router (B5/B6).

The grade-before-answer gate (the Refract loop, CLAUDE.md §4/§11 B5) is the
cheap-points move: before writing an answer, check the retrieved evidence
actually supports one. If not, abstain instead of hallucinating — that wins the
20 info_not_found questions outright and protects Correctness everywhere else.

Everything here works with OR without an LLM:
  - with an LLM: it grades support and writes a concise, cited answer, and may
    return NOT_FOUND to force an abstention.
  - without one: a heuristic grade (is there any real evidence?) plus an
    extractive answer (top document snippet, or a templated conflict summary).
"""

from __future__ import annotations

from src.common.schemas import DocHit, Edge
from src.llm.adapter import LLM

_MAX_CONTEXT_DOCS = 6
_NOT_FOUND = "I don't have enough information to answer that."


# --------------------------------------------------------------------------
# Context building
# --------------------------------------------------------------------------
def build_context(hits: list[DocHit], facts: list[str]) -> str:
    parts: list[str] = []
    if facts:
        parts.append("GRAPH FACTS (entity relationships, with dates and sources):")
        parts.extend(f"  - {f}" for f in facts)
    if hits:
        parts.append("\nDOCUMENTS (most relevant first):")
        for h in hits[:_MAX_CONTEXT_DOCS]:
            parts.append(f"  [{h.doc_id}] ({h.source_type}) {h.title}\n    {h.snippet}")
    return "\n".join(parts)


def format_edge_fact(edge: Edge, name_of) -> str:
    """One human-readable line for a graph edge, including supersession/provenance."""
    src, dst = name_of(edge.src_canonical_id), name_of(edge.dst_canonical_id)
    line = f"{src} {edge.rel_type} {dst}"
    if edge.valid_to is not None:
        line += f" (was true until {edge.valid_to:%Y-%m-%d}, now superseded)"
    elif edge.contested:
        line += " (CONTESTED — sources disagree)"
    else:
        line += " (current)"
    if edge.source_doc_ids:
        line += f" [source: {edge.source_type}, doc {edge.source_doc_ids[0]}]"
    return line


# --------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------
def grade(question: str, context: str, llm: LLM) -> tuple[bool, str]:
    """Does this evidence support an answer? (passed, reason)."""
    if not context.strip():
        return False, "no evidence retrieved"
    if not llm.available:
        # offline heuristic: having real context is enough to attempt an answer.
        return True, "heuristic: evidence present (no LLM grader configured)"
    verdict = llm.complete(
        f"Question: {question}\n\nEvidence:\n{context}\n\n"
        "Does the evidence above contain enough information to answer the question? "
        "Reply with exactly SUPPORTED or NOT_SUPPORTED, then a short reason.",
        system="You are a strict grader. Only SUPPORTED if the evidence directly answers the question.",
        max_tokens=120,
    )
    if not verdict:
        return True, "grader unavailable, proceeding"
    passed = verdict.strip().upper().startswith("SUPPORTED")
    return passed, verdict.strip()


# --------------------------------------------------------------------------
# Query rewriting (the critique step of the Refract retry loop, CLAUDE.md §4/B5)
# --------------------------------------------------------------------------
def rewrite_query(question: str, context: str, llm: LLM) -> list[str]:
    """Given a question whose first retrieval FAILED the grade, ask the LLM for
    better search queries. Returns up to 3 alternatives, or [] if no LLM.

    This is the 'critique agent' idea: instead of retrying the same words with a
    bigger result set (which finds more of the same misses), the LLM diagnoses
    what the first attempt lacked and rephrases — different wording for
    paraphrased/semantic questions, or a split into focused sub-queries for
    multi-part/multi-hop ones. Costs one LLM call, and only on questions that
    already failed, so the budget hit is small and targeted.

    Returns [] when no LLM is configured; the caller then falls back to the old
    broaden-the-result-set retry so behaviour still degrades gracefully offline.
    """
    if not llm.available:
        return []
    raw = llm.complete(
        f"Original question: {question}\n\n"
        "A first search returned evidence that did NOT clearly answer it. "
        f"First-attempt evidence (may be empty or off-topic):\n{context[:1200]}\n\n"
        "Write 1-3 alternative search queries that would find the missing "
        "information. Rephrase with different words, use likely synonyms or "
        "names, or split a multi-part question into focused sub-queries. "
        "Output ONLY the queries, one per line, no numbering or commentary.",
        system="You rewrite search queries to improve document retrieval. "
               "Output only queries, one per line.",
        max_tokens=150,
    )
    if not raw:
        return []
    out: list[str] = []
    for line in raw.splitlines():
        q = line.strip().lstrip("-*•0123456789. ").strip().strip('"')
        if q and q.lower() != question.lower() and q not in out:
            out.append(q)
    return out[:3]


# --------------------------------------------------------------------------
# Answer synthesis
# --------------------------------------------------------------------------
def synthesize(question: str, hits: list[DocHit], facts: list[str],
               llm: LLM) -> tuple[str, list[str], bool]:
    """Produce (answer_text, cited_doc_ids, abstained).

    Cites only documents that plausibly contributed (the scorer penalises padding).
    """
    context = build_context(hits, facts)
    if not context.strip():
        return _NOT_FOUND, [], True

    candidate_ids = [h.doc_id for h in hits[:_MAX_CONTEXT_DOCS]]

    if llm.available:
        answer = llm.complete(
            f"Question: {question}\n\nEvidence:\n{context}\n\n"
            "Answer the question in 1-3 sentences using ONLY the evidence. "
            "If the evidence does not answer it, reply exactly NOT_FOUND. "
            "When facts conflict, state the current one and note what it replaced, with dates.",
            system="You answer enterprise questions strictly from provided evidence. Never invent facts.",
            max_tokens=400,
        )
        if not answer or answer.strip().upper().startswith("NOT_FOUND"):
            return _NOT_FOUND, [], True
        # keep only cited docs that appear in the answer, else the top few used
        cited = [d for d in candidate_ids if d in answer] or candidate_ids[:3]
        return answer.strip(), cited, False

    # -- offline extractive fallback ---------------------------------------
    if facts:
        # a conflict/graph question we can answer from edges directly
        answer = " ".join(facts[:3])
        cited = candidate_ids[:3]
        return answer, cited, False
    top = hits[0]
    return (f"{top.title}: {top.snippet}").strip(), [top.doc_id], False
