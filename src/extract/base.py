"""Extractor framework shared by every per-source rule-based extractor (B1).

An "extractor" reads NormalizedDoc rows for one source and emits two kinds of
candidate rows:
  - Mention  : "this surface form, of this entity type, appears in this doc"
  - Relation : "these two mentions are related this way, in this doc"

Both always carry `doc_id` for provenance — the scorer checks cited documents,
so a candidate without a doc_id is a bug, not a nicety.

This module gives the per-source extractors:
  - a base class with the id-minting + emit helpers, so no extractor reinvents them
  - `load_ontology()` so extractors validate their types against the frozen schema
Nothing here does any LLM work; B1 is pure rules (CLAUDE.md §7.2).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.common.schemas import Mention, NormalizedDoc, Relation

# Repo-root-relative path to the frozen schema (Track B owns it).
_ONTOLOGY_PATH = Path(__file__).resolve().parents[2] / "ontology" / "ontology.yaml"


@lru_cache(maxsize=1)
def load_ontology() -> dict[str, Any]:
    """Parse ontology.yaml once and cache it.

    Extractors call this to check that an entity_type / rel_type they are about
    to emit is actually a declared type — a cheap guard against typos silently
    creating junk node types (exactly the open-schema drift we froze to avoid).
    """
    with _ONTOLOGY_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=1)
def valid_entity_types() -> frozenset[str]:
    onto = load_ontology()
    return frozenset(v["entity_type"] for v in onto["node_types"].values())


@lru_cache(maxsize=1)
def valid_rel_types() -> frozenset[str]:
    onto = load_ontology()
    return frozenset(k for k, v in onto["edge_types"].items() if v)


def mint_id(prefix: str, *parts: str) -> str:
    """Deterministic short id from its parts.

    Same inputs -> same id, always, so re-running extraction produces stable
    mention/relation ids and downstream stages can be idempotent. Uses a hash so
    ids stay short regardless of how long the surface form is.
    """
    h = hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{h}"


@dataclass
class ExtractionResult:
    """What one document (or one whole source) yields."""

    mentions: list[Mention] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)

    def extend(self, other: ExtractionResult) -> None:
        self.mentions.extend(other.mentions)
        self.relations.extend(other.relations)


class Extractor:
    """Base class for a per-source rule-based extractor.

    Subclasses set `source_type` and implement `extract_doc`. The base provides
    `emit_mention` / `emit_relation` helpers that mint stable ids, stamp
    provenance, and validate types against the ontology, so a subclass only
    writes the source-specific pattern logic.
    """

    source_type: str = "base"

    def __init__(self, *, strict: bool = False) -> None:
        # strict=True raises on an unknown entity/rel type; default just skips it,
        # so a single bad pattern can't halt a 500K-doc run.
        self.strict = strict
        self._entity_types = valid_entity_types()
        self._rel_types = valid_rel_types()

    # -- the one method subclasses must implement --------------------------
    def extract_doc(self, doc: NormalizedDoc) -> ExtractionResult:  # noqa: D401
        """Return the mentions + relations found in one document."""
        raise NotImplementedError

    def extract_many(self, docs: list[NormalizedDoc]) -> ExtractionResult:
        """Run over a batch; subclasses rarely need to override this."""
        out = ExtractionResult()
        for doc in docs:
            if doc.source_type != self.source_type:
                continue
            out.extend(self.extract_doc(doc))
        return out

    # -- emit helpers ------------------------------------------------------
    def emit_mention(self, doc: NormalizedDoc, surface_form: str, entity_type: str,
                     *, context: str = "", confidence: float = 1.0) -> Mention | None:
        surface_form = (surface_form or "").strip()
        if not surface_form:
            return None
        if entity_type not in self._entity_types:
            if self.strict:
                raise ValueError(f"{self.source_type}: unknown entity_type {entity_type!r}")
            return None
        return Mention(
            mention_id=mint_id("m", doc.doc_id, entity_type, surface_form.lower()),
            doc_id=doc.doc_id,
            source_type=self.source_type,
            surface_form=surface_form,
            entity_type=entity_type,
            context_snippet=context[:300],
            extractor="rule",
            confidence=confidence,
            timestamp=doc.timestamp,
        )

    def emit_relation(self, doc: NormalizedDoc, src: Mention | None, dst: Mention | None,
                      rel_type: str, *, evidence: str = "",
                      confidence: float = 1.0) -> Relation | None:
        if src is None or dst is None:
            return None
        if rel_type not in self._rel_types:
            if self.strict:
                raise ValueError(f"{self.source_type}: unknown rel_type {rel_type!r}")
            return None
        return Relation(
            relation_id=mint_id("r", doc.doc_id, rel_type, src.mention_id, dst.mention_id),
            src_mention_id=src.mention_id,
            dst_mention_id=dst.mention_id,
            rel_type=rel_type,
            doc_id=doc.doc_id,
            source_type=self.source_type,
            stated_at=doc.timestamp,
            evidence_snippet=evidence[:300],
            extractor="rule",
            confidence=confidence,
        )
