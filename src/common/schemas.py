"""The contract between Track A (infra) and Track B (AI).

FROZEN once committed — see CLAUDE.md §12 and §14.3.

These dataclasses are the only shapes that cross the boundary between the two
tracks. Track A writes NormalizedDoc; Track B writes Mention, Relation, Entity
and Edge; both read the rest. Changing a field here after ingestion starts means
re-running a pipeline stage on the other person's machine, so don't.

Each dataclass has PARQUET_COLUMNS listing the exact column order used on disk,
so both tracks read and write the same layout without coordinating.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

# --------------------------------------------------------------------------
# Source types. The nine EnterpriseRAG-Bench sources; also the key used by the
# conflict pass's source-priority table (CLAUDE.md §11, B4).
# --------------------------------------------------------------------------
# NOTE: "google_drive", not "drive" — this is the spelling questions.jsonl uses
# in its source_types field, verified against the v1.0.0 release.
SourceType = Literal[
    "slack",
    "gmail",
    "linear",
    "google_drive",
    "hubspot",
    "fireflies",
    "github",
    "jira",
    "confluence",
]

SOURCE_TYPES: tuple[str, ...] = (
    "slack",
    "gmail",
    "linear",
    "google_drive",
    "hubspot",
    "fireflies",
    "github",
    "jira",
    "confluence",
)

# How many of the 500 benchmark questions reference each source, measured from
# questions.jsonl v1.0.0. Wildly disproportionate to corpus size: confluence and
# jira are the two SMALLEST sources (~5K and ~6K docs) but carry the most
# questions, while slack is 275K docs for 79. Prioritise extraction accordingly.
QUESTION_WEIGHT: dict[str, int] = {
    "confluence": 114,
    "jira": 100,
    "slack": 79,
    "github": 60,
    "google_drive": 60,
    "linear": 58,
    "gmail": 55,
    "hubspot": 34,
    "fireflies": 25,
}

ExtractorKind = Literal["rule", "llm"]
ResolveMethod = Literal["exact", "splink", "llm"]


# --------------------------------------------------------------------------
# Stage 1 — Track A produces. data/normalized/{source}/part-*.parquet
# --------------------------------------------------------------------------
@dataclass(slots=True)
class NormalizedDoc:
    """One source document, parsed into the common shape.

    Deliberately *not* resolved: author_refs and mention_refs hold raw surface
    forms exactly as they appear ("Sam", "@soham", "S. Ratnaparkhi"). Deciding
    those are one person is Track B's job (B3), not the normalizer's.
    """

    doc_id: str                       # dsid_<sha>, parsed from the filename
    source_type: str                  # one of SOURCE_TYPES
    title: str                        # first line of the file
    body: str
    timestamp: datetime | None        # parsed from header fields where present
    author_refs: list[str] = field(default_factory=list)
    mention_refs: list[str] = field(default_factory=list)
    thread_id: str | None = None
    path: str = ""                    # original file path, for debugging
    raw_metadata: dict[str, Any] = field(default_factory=dict)  # verbatim `Key: value` lines

    PARQUET_COLUMNS = (
        "doc_id", "source_type", "title", "body", "timestamp",
        "author_refs", "mention_refs", "thread_id", "path", "raw_metadata",
    )


# --------------------------------------------------------------------------
# Stage 2 — Track B produces. data/candidates/*.parquet
# --------------------------------------------------------------------------
@dataclass(slots=True)
class Mention:
    """One occurrence of an entity in one document, before resolution."""

    mention_id: str
    doc_id: str                       # provenance — required, never blank
    source_type: str
    surface_form: str                 # exactly as written in the document
    entity_type: str                  # a node type from ontology/ontology.yaml
    context_snippet: str = ""         # surrounding text, used by LLM adjudication
    extractor: str = "rule"           # ExtractorKind
    confidence: float = 1.0
    timestamp: datetime | None = None

    PARQUET_COLUMNS = (
        "mention_id", "doc_id", "source_type", "surface_form", "entity_type",
        "context_snippet", "extractor", "confidence", "timestamp",
    )


@dataclass(slots=True)
class Relation:
    """One asserted relationship between two mentions, in one document."""

    relation_id: str
    src_mention_id: str
    dst_mention_id: str
    rel_type: str                     # an edge type from ontology/ontology.yaml
    doc_id: str                       # provenance — required
    source_type: str
    stated_at: datetime | None = None  # when the document says it was true
    evidence_snippet: str = ""
    extractor: str = "rule"
    confidence: float = 1.0

    PARQUET_COLUMNS = (
        "relation_id", "src_mention_id", "dst_mention_id", "rel_type",
        "doc_id", "source_type", "stated_at", "evidence_snippet",
        "extractor", "confidence",
    )


# --------------------------------------------------------------------------
# Stage 3 — Track B produces. data/resolved/*.parquet
# --------------------------------------------------------------------------
@dataclass(slots=True)
class Entity:
    """A canonical entity: many mentions merged into one node.

    Merging is non-destructive. Every surface form ever seen survives in
    aliases, because showing "Sam / @soham / S. Ratnaparkhi resolved to one
    person" is the demo (CLAUDE.md §6).
    """

    canonical_id: str
    entity_type: str
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    handles: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    mention_count: int = 0
    source_types: list[str] = field(default_factory=list)

    PARQUET_COLUMNS = (
        "canonical_id", "entity_type", "canonical_name", "aliases",
        "handles", "emails", "mention_count", "source_types",
    )


@dataclass(slots=True)
class Cluster:
    """Which mention landed in which canonical entity, and how it was decided.

    Kept separate from Entity so a wrong merge can be audited and undone.
    """

    canonical_id: str
    mention_id: str
    match_probability: float = 1.0
    method: str = "exact"             # ResolveMethod

    PARQUET_COLUMNS = ("canonical_id", "mention_id", "match_probability", "method")


# --------------------------------------------------------------------------
# Stage 4 — Track B produces, Track A loads. data/graph/edges.parquet
# --------------------------------------------------------------------------
@dataclass(slots=True)
class Edge:
    """A relationship between two canonical entities, with bi-temporal validity.

    The conflict model (CLAUDE.md §11, B4) never deletes: a contradicted edge
    keeps its row, gets valid_to set, and points superseded_by at the winner.
    That is what lets the system answer "Priya owns it now, Sam did until July"
    with both sources instead of silently picking one.

    valid_to is None  -> currently believed true
    contested is True -> sources genuinely disagree; show both, don't pick
    """

    edge_id: str
    src_canonical_id: str
    dst_canonical_id: str
    rel_type: str

    stated_at: datetime | None        # when the source says the fact held
    ingested_at: datetime             # when we learned it
    valid_from: datetime | None
    valid_to: datetime | None         # None = still current

    source_type: str
    source_doc_ids: list[str] = field(default_factory=list)  # provenance — required
    confidence: float = 1.0
    contested: bool = False
    superseded_by: str | None = None  # edge_id of the winning edge

    PARQUET_COLUMNS = (
        "edge_id", "src_canonical_id", "dst_canonical_id", "rel_type",
        "stated_at", "ingested_at", "valid_from", "valid_to",
        "source_type", "source_doc_ids", "confidence", "contested", "superseded_by",
    )

    @property
    def is_current(self) -> bool:
        return self.valid_to is None


# --------------------------------------------------------------------------
# Query-time types — Track A returns these from GraphClient.
# --------------------------------------------------------------------------
@dataclass(slots=True)
class DocHit:
    """A Layer 1 search result."""

    doc_id: str
    source_type: str
    title: str
    snippet: str
    score: float
    timestamp: datetime | None = None


@dataclass(slots=True)
class PathStep:
    """One hop of a graph path: an edge and the entity it lands on."""

    edge: Edge
    to_entity: Entity


@dataclass(slots=True)
class Path:
    """A bounded path returned by HydraDB's algo.SPpaths / SSpaths / MSpaths."""

    start: Entity
    steps: list[PathStep] = field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.steps)

    @property
    def doc_ids(self) -> list[str]:
        """Every document supporting this path — the citation list."""
        seen: list[str] = []
        for step in self.steps:
            for did in step.edge.source_doc_ids:
                if did not in seen:
                    seen.append(did)
        return seen


# --------------------------------------------------------------------------
# Answer types — Track B produces, Track A serves and scores.
# --------------------------------------------------------------------------
Route = Literal["lookup", "multihop", "conflict", "aggregate", "abstain"]


@dataclass(slots=True)
class ConflictView:
    """Two contradictory claims, both shown with provenance.

    The submission requirement is to *surface* the disagreement, not to silently
    resolve it (CLAUDE.md §2).
    """

    current: Edge
    superseded: list[Edge] = field(default_factory=list)
    explanation: str = ""


@dataclass(slots=True)
class AnswerTrace:
    """Why the system answered the way it did. Rendered by the UI; written to
    traces.jsonl alongside the answers."""

    route: str = "lookup"             # Route
    retrieved_doc_ids: list[str] = field(default_factory=list)
    entity_ids: list[str] = field(default_factory=list)
    paths: list[Path] = field(default_factory=list)
    conflicts: list[ConflictView] = field(default_factory=list)
    grade_passed: bool = True         # did the evidence support an answer?
    grade_reason: str = ""
    retries: int = 0
    llm_calls: int = 0


@dataclass(slots=True)
class AnswerResult:
    """The single value Track B returns to Track A.

    src.agent.router.answer() is the entire A -> B call surface (§14.3).
    Everything behind it is Track B's to restructure freely.

    document_ids goes straight into answers.jsonl, and the scorer penalises
    invalid extra documents — cite only what genuinely contributed.
    """

    question_id: str
    answer: str
    document_ids: list[str] = field(default_factory=list)
    abstained: bool = False
    confidence: float = 1.0
    trace: AnswerTrace = field(default_factory=AnswerTrace)

    def to_answer_jsonl(self) -> dict[str, Any]:
        """The exact line format the benchmark expects (CLAUDE.md §3.1)."""
        return {
            "question_id": self.question_id,
            "answer": self.answer,
            "document_ids": self.document_ids,
        }
