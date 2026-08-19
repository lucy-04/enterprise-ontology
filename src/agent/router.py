"""The single entry point Track A calls into Track B's code.

    answer(question, client) -> AnswerResult

This ONE function is the entire A -> B call surface (CLAUDE.md §14.3). Track A's
FastAPI `POST /ask` and the eval runner call nothing else in Track B. Everything
behind this function — routing, retrieval, grading, synthesis — is Track B's to
restructure freely without touching Track A.

Right now this is a stub: it classifies the question and abstains, so the whole
pipeline runs end-to-end (A can wire up its API and eval today) while the real
logic lands in B5/B6. Replacing the body never changes the signature.
"""

from __future__ import annotations

from src.common.schemas import AnswerResult, AnswerTrace
from src.graph.client import GraphClient, NotBuiltYetError


def answer(question: str, client: GraphClient,
           question_id: str = "") -> AnswerResult:
    """Answer one benchmark question against the two retrieval layers.

    Parameters
    ----------
    question:
        The natural-language question text.
    client:
        Track A's GraphClient — the only way this code reaches the search index
        (Layer 1) or the HydraDB graph (Layer 2). Never open a DB connection here.
    question_id:
        The benchmark id (e.g. "qst_0001"), passed straight through into the
        answer line so Track A's eval runner can write answers.jsonl.

    Returns
    -------
    AnswerResult
        Carries `answer` text, the `document_ids` that genuinely contributed
        (the scorer penalises padding), and a `trace` the UI renders.

    Contract note: this must NEVER raise. A failure to answer is an abstention,
    not an exception — the eval runner scores every one of the 500 questions and
    an unhandled error zeroes the whole run. Catch everything, abstain on error.
    """
    trace = AnswerTrace(route="abstain")

    try:
        # --- STUB BEHAVIOUR (B5/B6 will replace everything below) ----------
        # For now: try a Layer-1 search so the plumbing is exercised, but always
        # abstain, because no real synthesis exists yet. This lets Track A build
        # and test the API/eval loop against a function that returns valid,
        # well-formed AnswerResults today.
        hits = []
        try:
            hits = client.search(question, k=10)
        except NotBuiltYetError:
            # Layer 1 not implemented yet on A's side — fine, still abstain cleanly.
            pass

        trace.retrieved_doc_ids = [h.doc_id for h in hits]
        trace.grade_passed = False
        trace.grade_reason = "router not implemented yet (B5/B6 pending); abstaining by design"

        return AnswerResult(
            question_id=question_id,
            answer="I don't have enough information to answer that.",
            document_ids=[],
            abstained=True,
            confidence=0.0,
            trace=trace,
        )
    except Exception as exc:  # never let one bad question kill the eval run
        trace.grade_reason = f"router error, abstained: {exc!r}"
        return AnswerResult(
            question_id=question_id,
            answer="I don't have enough information to answer that.",
            document_ids=[],
            abstained=True,
            confidence=0.0,
            trace=trace,
        )
