"""A3 — query side of Layer 1. Backs GraphClient.search() and .get_docs().

Hybrid retrieval: FTS5 keyword results and vector results are combined by
**reciprocal rank fusion** rather than by blending scores. RRF only needs each
system's *ranking*, which matters because bm25 scores and cosine similarities
are not on comparable scales and normalising them is guesswork that quietly
biases one system. RRF is also what makes the two complementary — a document
that both systems rank moderately well beats one that only keyword search loves.

    score(d) = sum over systems of 1 / (k + rank(d))     k = 60

The two systems fail in opposite directions, which is the point of running both:
keyword search misses paraphrases ("who owns billing" vs "billing ownership"),
and vector search misses exact identifiers (ticket ids like ENG-30521, error
strings, filenames) because they carry little semantic signal.

The index is opened lazily and the vector matrix is memory-mapped, so importing
this module costs nothing and a keyword-only query never loads the model.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from src.common.config import settings
from src.common.schemas import DocHit, NormalizedDoc
from src.index.build import db_path, meta_path, vectors_path

# FTS5 treats a pile of punctuation as query syntax. Queries arrive as natural
# language, so terms are extracted and requoted rather than escaped in place.
_TERM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-\.]*")

# Words that match most documents and only dilute the ranking.
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "was", "were", "be", "been", "what", "which", "who", "whom", "whose", "when",
    "where", "why", "how", "did", "does", "do", "with", "that", "this", "it",
    "at", "by", "from", "as", "we", "our", "us",
}


def fts_query(text: str) -> str:
    """Natural-language question -> an FTS5 MATCH expression.

    Terms are OR-ed, not AND-ed: a question phrased differently from the
    document would return nothing under AND, and bm25 already ranks documents
    matching more terms higher. Recall is what Layer 1 is for.
    """
    terms = [t.lower() for t in _TERM_RE.findall(text or "")]
    kept = [t for t in terms if t not in _STOP and len(t) > 1]
    if not kept:
        kept = terms
    # Quote every term so an id like "ENG-30521" is a literal, not a NOT.
    return " OR ".join(f'"{t}"' for t in dict.fromkeys(kept))


def rrf(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal rank fusion over several ranked id lists."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


class SearchIndex:
    """Lazily-opened handle on the SQLite + vector index."""

    def __init__(self, index_dir: str | Path | None = None) -> None:
        self._dir = Path(index_dir) if index_dir else None
        self._conn: sqlite3.Connection | None = None
        self._vectors: np.memmap | None = None
        self._model = None
        self._meta: dict | None = None

    # -- handles ------------------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            path = (self._dir / "docs.sqlite") if self._dir else db_path()
            if not path.exists():
                raise FileNotFoundError(
                    f"no search index at {path} — run `just index`")
            self._conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True,
                                         check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    @property
    def meta(self) -> dict:
        if self._meta is None:
            path = (self._dir / "index_meta.json") if self._dir else meta_path()
            self._meta = json.loads(path.read_text()) if path.exists() else {}
        return self._meta

    @property
    def vectors(self) -> np.memmap | None:
        """Memory-mapped, so the matrix is never fully resident."""
        if self._vectors is None:
            path = (self._dir / "vectors.f16") if self._dir else vectors_path()
            count, dim = self.meta.get("count"), self.meta.get("dim")
            if not path.exists() or not count or not dim:
                return None
            self._vectors = np.memmap(path, dtype=np.float16, mode="r",
                                      shape=(int(count), int(dim)))
        return self._vectors

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            from src.index.build import _pick_device

            name = self.meta.get("model") or settings()["index"]["embed_model"]
            self._model = SentenceTransformer(name, device=_pick_device())
        return self._model

    # -- retrieval ----------------------------------------------------------
    def keyword(self, query: str, k: int,
                sources: list[str] | None = None) -> list[tuple[str, float]]:
        match = fts_query(query)
        if not match:
            return []
        sql = ("SELECT d.doc_id AS doc_id, bm25(docs_fts) AS score "
               "FROM docs_fts JOIN docs d ON d.rowid = docs_fts.rowid "
               "WHERE docs_fts MATCH ?")
        params: list = [match]
        if sources:
            placeholders = ",".join("?" for _ in sources)
            sql += f" AND d.source_type IN ({placeholders})"
            params += list(sources)
        # bm25() is negative in SQLite, most relevant is most negative.
        sql += " ORDER BY score LIMIT ?"
        params.append(k)
        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(r["doc_id"], -float(r["score"])) for r in rows]

    def vector(self, query: str, k: int,
               sources: list[str] | None = None) -> list[tuple[str, float]]:
        vectors = self.vectors
        if vectors is None:
            return []
        embedding = self.model.encode([query], convert_to_numpy=True,
                                      normalize_embeddings=True)[0].astype(np.float32)

        # Chunked brute force: the matrix stays memory-mapped and only one
        # chunk is ever resident, which is what keeps this viable on 8GB.
        best_scores = np.empty(0, dtype=np.float32)
        best_rows = np.empty(0, dtype=np.int64)
        chunk = 50_000
        for start in range(0, vectors.shape[0], chunk):
            block = np.asarray(vectors[start:start + chunk], dtype=np.float32)
            sims = block @ embedding
            take = min(k, sims.shape[0])
            top = np.argpartition(-sims, take - 1)[:take]
            best_scores = np.concatenate([best_scores, sims[top]])
            best_rows = np.concatenate([best_rows, top + start])

        if best_rows.size == 0:
            return []
        order = np.argsort(-best_scores)[:k * 4 if sources else k]
        rowids = [int(best_rows[i]) + 1 for i in order]
        scores = {rid: float(best_scores[i]) for rid, i in zip(rowids, order, strict=True)}

        placeholders = ",".join("?" for _ in rowids)
        sql = f"SELECT rowid, doc_id, source_type FROM docs WHERE rowid IN ({placeholders})"
        rows = self.conn.execute(sql, rowids).fetchall()
        out = [(r["doc_id"], scores.get(r["rowid"], 0.0)) for r in rows
               if not sources or r["source_type"] in sources]
        out.sort(key=lambda pair: -pair[1])
        return out[:k]

    def search(self, query: str, k: int = 20,
               sources: list[str] | None = None) -> list[DocHit]:
        """Hybrid search. Over-fetches from each system so fusion has room."""
        if not (query or "").strip():
            return []
        pool = max(k * 3, 30)
        keyword = self.keyword(query, pool, sources)
        vector = self.vector(query, pool, sources)
        if not keyword and not vector:
            return []

        fused = rrf([[d for d, _ in keyword], [d for d, _ in vector]],
                    k=int(settings()["index"].get("rrf_k", 60)))
        top = sorted(fused.items(), key=lambda kv: -kv[1])[:k]
        if not top:
            return []

        placeholders = ",".join("?" for _ in top)
        rows = self.conn.execute(
            f"SELECT doc_id, source_type, title, body, ts FROM docs "
            f"WHERE doc_id IN ({placeholders})", [d for d, _ in top]).fetchall()
        by_id = {r["doc_id"]: r for r in rows}

        hits: list[DocHit] = []
        for doc_id, score in top:
            row = by_id.get(doc_id)
            if row is None:
                continue
            hits.append(DocHit(
                doc_id=doc_id,
                source_type=row["source_type"],
                title=row["title"] or "",
                snippet=_snippet(row["body"] or "", query),
                score=float(score),
                timestamp=_ts(row["ts"]),
            ))
        return hits

    def get_docs(self, doc_ids: list[str]) -> list[NormalizedDoc]:
        """Full documents by id, in the order asked for."""
        wanted = [d for d in (doc_ids or []) if d]
        if not wanted:
            return []
        placeholders = ",".join("?" for _ in wanted)
        rows = self.conn.execute(
            f"SELECT * FROM docs WHERE doc_id IN ({placeholders})", wanted).fetchall()
        by_id = {r["doc_id"]: r for r in rows}
        out: list[NormalizedDoc] = []
        for doc_id in wanted:
            row = by_id.get(doc_id)
            if row is None:
                continue
            out.append(NormalizedDoc(
                doc_id=row["doc_id"],
                source_type=row["source_type"],
                title=row["title"] or "",
                body=row["body"] or "",
                timestamp=_ts(row["ts"]),
                author_refs=_from_json(row["author_refs"]),
                mention_refs=_from_json(row["mention_refs"]),
                thread_id=row["thread_id"],
                path=row["path"] or "",
                raw_metadata=_from_json(row["raw_metadata"], as_dict=True),
            ))
        return out

    def count(self) -> int:
        return int(self.conn.execute("SELECT count(*) FROM docs").fetchone()[0])

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._vectors = None


# ---------------------------------------------------------------------------
def _ts(value) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None


def _from_json(value, as_dict: bool = False):
    if not value:
        return {} if as_dict else []
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {} if as_dict else []


def _snippet(body: str, query: str, width: int = 320) -> str:
    """A window around the first query term, so the UI shows why it matched."""
    terms = [t.lower() for t in _TERM_RE.findall(query or "")
             if t.lower() not in _STOP and len(t) > 2]
    low = body.lower()
    for term in terms:
        at = low.find(term)
        if at >= 0:
            start = max(0, at - width // 3)
            text = body[start:start + width].replace("\n", " ").strip()
            return ("..." if start else "") + text + ("..." if start + width < len(body) else "")
    return body[:width].replace("\n", " ").strip()
