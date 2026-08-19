"""The single entry point Track A calls into Track B's code (CLAUDE.md §14.3).

    answer(question, client) -> AnswerResult

This ONE function is the entire A -> B call surface. Track A's FastAPI POST /ask
and the eval runner call nothing else in Track B; everything behind here is ours.

Pipeline (B5/B6):
  1. classify the question -> a retrieval strategy (lookup/multihop/conflict/aggregate)
  2. retrieve evidence through the GraphClient (Layer 1 search + Layer 2 graph),
     degrading gracefully if a client method isn't implemented yet
  3. GRADE the evidence before answering; on failure, one retry, then ABSTAIN
  4. synthesize an answer that cites only the documents that contributed
It NEVER raises: a failure is an abstention, because the eval scores all 500
questions and one unhandled error would zero the run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from src.agent.classify import classify_route, question_entities
from src.agent.synthesize import format_edge_fact, grade, synthesize
from src.common.schemas import AnswerResult, AnswerTrace, DocHit
from src.graph.client import GraphClient

T = TypeVar("T")

# Relation types worth pulling as "facts about" an entity for conflict/lookup answers.
_FACT_RELS = ("MEMBER_OF", "WORKS_FOR", "OWNS", "HAS_ROLE", "ASSIGNED_TO", "REPORTS_TO")
_ABSTAIN = "I don't have enough information to answer that."


def _safe(fn: Callable[[], T]) -> T | None:
    """Call a client method; swallow NotBuiltYet/other errors so retrieval degrades."""
    try:
        return fn()
    except Exception:
        return None


def _gather(question: str, route: str, client: GraphClient,
            trace: AnswerTrace, k: int = 12) -> tuple[list[DocHit], list[str]]:
    """Retrieve evidence: Layer 1 hits + Layer 2 graph facts. Populates trace."""
    hits: list[DocHit] = _safe(lambda: client.search(question, k=k)) or []

    resolved = []
    for name in question_entities(question):
        resolved += _safe(lambda n=name: client.find_entity(n)) or []
    # dedupe entities by canonical_id
    seen: dict[str, object] = {}
    for e in resolved:
        seen.setdefault(e.canonical_id, e)
    resolved = list(seen.values())
    trace.entity_ids = [e.canonical_id for e in resolved]
    name_of = {e.canonical_id: e.canonical_name for e in resolved}

    def naming(cid: str) -> str:
        return name_of.get(cid, cid)

    facts: list[str] = []
    # conflict/lookup: the current + superseded facts about each named entity
    if route in ("conflict", "lookup", "aggregate") and resolved:
        for e in resolved:
            for rel in _FACT_RELS:
                for edge in _safe(lambda e=e, rel=rel: client.facts_about(e.canonical_id, rel)) or []:
                    facts.append(format_edge_fact(edge, naming))

    # multihop: bounded paths between the first two resolved entities
    if route == "multihop" and len(resolved) >= 2:
        paths = _safe(lambda: client.paths([resolved[0].canonical_id],
                                           [resolved[1].canonical_id], max_len=3)) or []
        for p in paths[:5]:
            for step in p.steps:
                facts.append(format_edge_fact(step.edge, naming))
        trace.paths = list(paths[:5])
        # pull the documents backing those paths into the citation pool
        path_doc_ids = {d for p in paths[:5] for d in p.doc_ids}
        hit_ids = {h.doc_id for h in hits}
        for did in path_doc_ids - hit_ids:
            hits.append(DocHit(doc_id=did, source_type="", title="", snippet="", score=0.0))

    trace.retrieved_doc_ids = [h.doc_id for h in hits]
    # dedupe facts, keep order
    facts = list(dict.fromkeys(facts))
    return hits, facts


def answer(question: str, client: GraphClient,
           question_id: str = "") -> AnswerResult:
    from src.llm.adapter import LLM

    trace = AnswerTrace(route="lookup")
    try:
        llm = LLM("strong")
        route = classify_route(question)
        trace.route = route

        hits, facts = _gather(question, route, client, trace)

        passed, reason = grade(question, _context(hits, facts), llm)
        trace.grade_passed, trace.grade_reason = passed, reason

        # one retry: broaden retrieval before giving up (the Refract retry step)
        if not passed:
            trace.retries = 1
            more = _safe(lambda: client.search(question, k=25)) or []
            hit_ids = {h.doc_id for h in hits}
            hits += [h for h in more if h.doc_id not in hit_ids]
            passed, reason = grade(question, _context(hits, facts), llm)
            trace.grade_passed, trace.grade_reason = passed, reason

        if not passed:
            trace.llm_calls = llm.calls
            return AnswerResult(question_id, _ABSTAIN, [], abstained=True,
                                confidence=0.0, trace=trace)

        answer_text, cited, abstained = synthesize(question, hits, facts, llm)
        trace.llm_calls = llm.calls
        confidence = 0.0 if abstained else (0.85 if llm.available else 0.55)
        return AnswerResult(question_id, answer_text, cited, abstained=abstained,
                            confidence=confidence, trace=trace)
    except Exception as exc:  # never let one question kill the eval run
        trace.grade_reason = f"router error, abstained: {exc!r}"
        return AnswerResult(question_id, _ABSTAIN, [], abstained=True,
                            confidence=0.0, trace=trace)


def _context(hits, facts):
    from src.agent.synthesize import build_context
    return build_context(hits, facts)
