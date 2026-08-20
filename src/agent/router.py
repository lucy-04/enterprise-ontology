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

from src.agent.classify import classify_route, question_entities
from src.agent.synthesize import format_edge_fact, grade, synthesize
from src.common.schemas import AnswerResult, AnswerTrace, DocHit
from src.graph.client import GraphClient

# Relation types worth pulling as "facts about" an entity for conflict/lookup answers.
_FACT_RELS = ("MEMBER_OF", "WORKS_FOR", "OWNS", "HAS_ROLE", "ASSIGNED_TO", "REPORTS_TO")
_ABSTAIN = "I don't have enough information to answer that."


def _safe[T](fn: Callable[[], T]) -> T | None:
    """Call a client method; swallow NotBuiltYet/other errors so retrieval degrades."""
    try:
        return fn()
    except Exception:
        return None


def _make_naming(client: GraphClient, name_of: dict[str, str]) -> Callable[[str], str]:
    """Return a cid -> name resolver backed by (and caching into) name_of.

    Entities on the *far* end of an edge (e.g. the teams Alex is a MEMBER_OF)
    were never resolved from the question, so they are absent from name_of.
    Without this graph fallback the LLM is handed a raw canonical id and repeats
    it back verbatim. get_entity is memoised into name_of, so each unknown id
    costs at most one graph lookup — and the cache is shared across the first
    attempt and any rewrite retries.
    """
    def naming(cid: str) -> str:
        if cid not in name_of:
            entity = _safe(lambda: client.get_entity(cid))
            name_of[cid] = entity.canonical_name if entity else cid
        return name_of[cid]
    return naming


def _entity_facts(query: str, route: str, client: GraphClient,
                  name_of: dict[str, str]) -> tuple[list, list[str], list]:
    """Resolve the entities named in `query` and pull their graph facts.

    Returns (resolved_entities, fact_lines, paths). name_of is shared/mutated so
    id->name resolution is cached across the first attempt and rewrite retries.
    """
    naming = _make_naming(client, name_of)
    resolved = []
    for name in question_entities(query):
        resolved += _safe(lambda n=name: client.find_entity(n)) or []
    seen: dict[str, object] = {}
    for e in resolved:
        seen.setdefault(e.canonical_id, e)
    resolved = list(seen.values())
    for e in resolved:                       # seed the cache with names we know
        name_of.setdefault(e.canonical_id, e.canonical_name)

    facts: list[str] = []
    paths: list = []
    # conflict/lookup: the current + superseded facts about each named entity
    if route in ("conflict", "lookup", "aggregate") and resolved:
        for e in resolved:
            for rel in _FACT_RELS:
                for edge in _safe(lambda e=e, rel=rel: client.facts_about(e.canonical_id, rel)) or []:
                    facts.append(format_edge_fact(edge, naming))
    # multihop: bounded paths between the first two resolved entities
    if route == "multihop" and len(resolved) >= 2:
        found = _safe(lambda: client.paths([resolved[0].canonical_id],
                                           [resolved[1].canonical_id], max_len=3)) or []
        for p in found[:5]:
            for step in p.steps:
                facts.append(format_edge_fact(step.edge, naming))
        paths = list(found[:5])
    return resolved, facts, paths


def _merge_hits(hits: list[DocHit], more: list[DocHit]) -> list[DocHit]:
    seen = {h.doc_id for h in hits}
    hits += [h for h in more if h.doc_id not in seen]
    return hits


def _gather(question: str, route: str, client: GraphClient, trace: AnswerTrace,
            k: int = 12, name_of: dict[str, str] | None = None) -> tuple[list[DocHit], list[str]]:
    """Retrieve evidence: Layer 1 hits + Layer 2 graph facts. Populates trace."""
    if name_of is None:
        name_of = {}
    hits: list[DocHit] = _safe(lambda: client.search(question, k=k)) or []
    resolved, facts, paths = _entity_facts(question, route, client, name_of)
    trace.entity_ids = [e.canonical_id for e in resolved]

    if paths:
        trace.paths = paths
        # pull the documents backing those paths into the citation pool
        path_doc_ids = {d for p in paths for d in p.doc_ids}
        hit_ids = {h.doc_id for h in hits}
        for did in path_doc_ids - hit_ids:
            hits.append(DocHit(doc_id=did, source_type="", title="", snippet="", score=0.0))

    trace.retrieved_doc_ids = [h.doc_id for h in hits]
    facts = list(dict.fromkeys(facts))       # dedupe, keep order
    return hits, facts


def answer(question: str, client: GraphClient,
           question_id: str = "") -> AnswerResult:
    from src.agent.synthesize import rewrite_query
    from src.llm.adapter import LLM

    trace = AnswerTrace(route="lookup")
    try:
        llm = LLM("strong")
        route = classify_route(question)
        trace.route = route

        name_of: dict[str, str] = {}
        hits, facts = _gather(question, route, client, trace, name_of=name_of)

        passed, reason = grade(question, _context(hits, facts), llm)
        trace.grade_passed, trace.grade_reason = passed, reason

        # One retry — the Refract critique/retry step. With an LLM, critique the
        # failure and REWRITE the query (rephrase / decompose), then retrieve on
        # the rewrites; without an LLM, fall back to simply broadening the search.
        if not passed:
            trace.retries = 1
            rewrites = rewrite_query(question, _context(hits, facts), llm)
            if rewrites:
                for q in rewrites:
                    hits = _merge_hits(hits, _safe(lambda q=q: client.search(q, k=12)) or [])
                    _, more_facts, _ = _entity_facts(q, route, client, name_of)
                    facts = list(dict.fromkeys(facts + more_facts))
                note = "retried after query rewrite: " + " | ".join(rewrites)
            else:
                hits = _merge_hits(hits, _safe(lambda: client.search(question, k=25)) or [])
                note = "retried with broader search (no LLM rewrite)"
            trace.retrieved_doc_ids = [h.doc_id for h in hits]
            passed, reason = grade(question, _context(hits, facts), llm)
            trace.grade_passed = passed
            trace.grade_reason = f"{reason}  [{note}]"

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
