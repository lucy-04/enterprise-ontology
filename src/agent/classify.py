"""Question classification + entity extraction for the router (B5).

Two cheap, no-LLM helpers:
  - classify_route(): which retrieval strategy the question needs
      lookup    -> a fact in one/few docs; Layer 1 search answers it
      multihop  -> connect 2+ entities; needs graph paths (Layer 2)
      conflict  -> two sources disagree; needs the bi-temporal facts (Layer 2)
      aggregate -> count / list-all; needs a graph aggregate
    (abstain is decided later, by the grade gate, not here.)
  - question_entities(): candidate entity surface forms to resolve against the
    graph (quoted names, Capitalized spans, @handles, ticket/PR ids, emails).

Keyword heuristics are deliberately simple and fast; the router does not fully
trust the label — it always also runs Layer 1 search — so a misclassification
degrades gracefully rather than failing.
"""

from __future__ import annotations

import re

from src.ingest.normalize import EMAIL_RE, PR_RE, TICKET_RE

_CONFLICT_CUES = (
    "current", "currently", "now", "still", "changed", "change", "used to",
    "previously", "latest", "up to date", "up-to-date", "no longer", "anymore",
    "which is correct", "conflict", "contradict", "who owns", "who is the owner",
    "most recent", "as of", "today",
)
_AGGREGATE_CUES = (
    "how many", "how much", "count", "number of", "list all", "list every",
    "all the", "every ", "total", "which ones", "name all", "what are all",
)
_MULTIHOP_CUES = (
    " who ", "through", "connected", "related to", "linked", "reported by",
    "assigned to", "worked on", "involved in", "responsible for", "between",
    "same ", "both ", "that also", "which team", "whose",
)

_STOPWORDS_CAP = {"The", "A", "An", "What", "Who", "Which", "When", "Where",
                  "Why", "How", "Is", "Are", "Was", "Were", "Did", "Does",
                  "Do", "Can", "Will", "Would", "Should", "In", "On", "At",
                  "For", "Of", "To", "And", "Or", "But", "This", "That"}


def classify_route(question: str) -> str:
    q = question.lower()
    if any(cue in q for cue in _AGGREGATE_CUES):
        return "aggregate"
    if any(cue in q for cue in _CONFLICT_CUES):
        return "conflict"
    if any(cue in q for cue in _MULTIHOP_CUES):
        return "multihop"
    return "lookup"


def question_entities(question: str) -> list[str]:
    """Best-effort candidate entity surface forms mentioned in the question."""
    ents: list[str] = []

    # explicit ids first — highest precision
    ents += TICKET_RE.findall(question)
    ents += [f"PR#{n}" for n in PR_RE.findall(question)]
    ents += EMAIL_RE.findall(question)

    # quoted spans: "Redwood Inference", 'the KMS service'
    ents += re.findall(r'"([^"]{2,40})"', question)
    ents += re.findall(r"'([^']{2,40})'", question)

    # @handles
    ents += re.findall(r"(?<![\w@])@([A-Za-z][\w.\-]{1,30})", question)

    # Capitalized spans not at sentence start / not question words
    for span in re.findall(r"\b([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*)", question):
        first = span.split()[0]
        if first in _STOPWORDS_CAP and " " not in span:
            continue
        # drop a leading question word from a multiword span ("Who Sam" -> "Sam")
        toks = [t for t in span.split() if t not in _STOPWORDS_CAP]
        if toks:
            ents.append(" ".join(toks))

    # dedupe, preserve order
    seen: dict[str, None] = {}
    for e in ents:
        e = e.strip()
        if e and e not in seen:
            seen[e] = None
    return list(seen)
