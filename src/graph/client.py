"""GraphClient — the Track A -> Track B interface.

FROZEN signatures (CLAUDE.md §12, §14.3). Track B codes against this from night
one; Track A fills in the bodies. Only the bodies change, never the signatures.

Layer 1 (search, get_docs) hits the SQLite FTS5 + vector index over all ~500K
documents. Layer 2 (everything else) hits HydraDB over Bolt. Which one a
question needs is the router's decision (Track B, B5), not this class's.
"""

from __future__ import annotations

from datetime import datetime

from src.common.schemas import DocHit, Edge, Entity, NormalizedDoc, Path


class NotBuiltYetError(NotImplementedError):
    """Raised by a stub whose Track A implementation has not landed.

    Track B: catching this and falling back to fixture data is a reasonable way
    to keep moving before the real implementation exists.
    """


class GraphClient:
    """Everything Track B needs to retrieve evidence.

    Track B never opens a Bolt session, a SQLite connection, or a Parquet file
    directly — it goes through here, so Track A can change storage without
    breaking the agent.
    """

    def __init__(self, uri: str | None = None, user: str | None = None,
                 password: str | None = None, index_dir: str | None = None) -> None:
        self.uri = uri
        self.user = user
        self.password = password
        self.index_dir = index_dir

    # -- Layer 1: full-corpus search ---------------------------------------

    def search(self, query: str, k: int = 20,
               sources: list[str] | None = None) -> list[DocHit]:
        """Hybrid keyword + vector search over all ~500K documents.

        Scores are reciprocal-rank-fused across FTS5 and the vector index.
        This is the workhorse for simple lookup questions and the main
        protection for the Document Recall metric.
        """
        raise NotBuiltYetError("A3")

    def get_docs(self, doc_ids: list[str]) -> list[NormalizedDoc]:
        """Fetch full normalized documents by id. Order follows doc_ids."""
        raise NotBuiltYetError("A3")

    # -- Layer 2: the ontology graph in HydraDB -----------------------------

    def find_entity(self, name_or_alias: str,
                    type: str | None = None) -> list[Entity]:
        """Resolve a surface form to canonical entities.

        Alias-aware by design: "Sam", "@soham" and "S. Ratnaparkhi" must all
        return the same entity. This is the entry point for nearly every
        graph question.
        """
        raise NotBuiltYetError("A5")

    def neighbors(self, cid: str, rel_types: list[str] | None = None,
                  at_time: datetime | None = None,
                  include_invalid: bool = False) -> list[Edge]:
        """Edges touching one entity.

        at_time -> "as of" query against the bi-temporal model: return edges
        whose validity window contains that instant. Default (None) means
        currently-valid edges only.

        include_invalid=True also returns superseded edges, which is what
        conflict answers need.
        """
        raise NotBuiltYetError("A5")

    def paths(self, src_ids: list[str], dst_ids: list[str], max_len: int = 3,
              rel_types: list[str] | None = None) -> list[Path]:
        """Bounded multi-hop paths between entity sets.

        Wraps HydraDB's native algo.SPpaths / algo.SSpaths / algo.MSpaths and
        picks the right one from the shape of src_ids/dst_ids. This is the
        graph-native core of the submission — do not replace with hand-rolled
        BFS (CLAUDE.md §5, §11 A5).
        """
        raise NotBuiltYetError("A5")

    def facts_about(self, cid: str, rel_type: str) -> list[Edge]:
        """Every assertion of one relation type about one entity, current and
        superseded, newest first.

        Conflict questions are answered from this: the current edge plus what it
        replaced, each with its source and date.
        """
        raise NotBuiltYetError("A5")

    def cypher(self, query: str, params: dict | None = None) -> list[dict]:
        """Escape hatch for anything the typed methods don't cover.

        Fine to use, but if Track B calls this repeatedly for the same shape of
        question, tell Track A and it becomes a real method.
        """
        raise NotBuiltYetError("A5")

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Release the Bolt driver and any open index handles."""
        return None

    def __enter__(self) -> GraphClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
