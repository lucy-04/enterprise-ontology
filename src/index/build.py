"""A3 — build Layer 1: the full-corpus search index.  `just index`

Two indexes over every normalized document, fused at query time:

  * **SQLite FTS5** for keyword search. No server, no extra dependency, and it
    handles 500K rows without tuning. Stored as an external-content table so the
    document text is not duplicated between the table and the index.

  * **A float16 vector memmap** for semantic search. Embeddings come from a
    local sentence-transformers model, so this costs nothing and needs no API
    quota. Searched by chunked brute force rather than an ANN library: at
    500K x 384 the matrix is ~384MB on disk and a query is ~50ms, which is well
    inside budget and removes a whole class of install risk on an 8GB machine.

Why Layer 1 exists at all: the benchmark scores Document Recall separately from
answer correctness, and ~300 of the 500 questions are plain lookups. Those are
won by finding the right document, not by reasoning over a graph. The graph
(Layer 2) handles what search structurally cannot — multi-hop, conflicts, and
abstention. See CLAUDE.md §7.1.

Memory: everything streams. Documents go into SQLite in batches and embeddings
are written straight into the memmap, so peak RSS is one batch, not the corpus.

Usage:
    python -m src.index.build                    # everything under data/normalized
    python -m src.index.build --sources jira     # one source
    python -m src.index.build --skip-vectors     # FTS5 only (fast, no model)
    python -m src.index.build --limit 5000       # dev subset
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from tqdm import tqdm

from src.common.config import data_dir, settings

# One row of metadata per document, plus the text FTS5 indexes.
SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    rowid        INTEGER PRIMARY KEY,
    doc_id       TEXT UNIQUE NOT NULL,
    source_type  TEXT NOT NULL,
    title        TEXT,
    body         TEXT,
    ts           INTEGER,
    thread_id    TEXT,
    path         TEXT,
    author_refs  TEXT,
    mention_refs TEXT,
    raw_metadata TEXT
);
CREATE INDEX IF NOT EXISTS docs_source ON docs(source_type);
"""

# external content: FTS5 reads the text from `docs` rather than storing a second
# copy, which roughly halves the index on disk.
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    title, body,
    content='docs', content_rowid='rowid',
    tokenize='porter unicode61'
);
"""


def index_dir() -> Path:
    d = data_dir() / "index"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return index_dir() / "docs.sqlite"


def vectors_path() -> Path:
    return index_dir() / "vectors.f16"


def meta_path() -> Path:
    return index_dir() / "index_meta.json"


# ---------------------------------------------------------------------------
# keyword index
# ---------------------------------------------------------------------------
def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    # Bulk-load settings. Durability does not matter here: the index is derived
    # data and is rebuilt from Parquet whenever it is wrong.
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA cache_size = -64000")
    return conn


def _to_json(value) -> str:
    """Parquet list/dict columns -> JSON text. Never trust truthiness of a
    pandas/numpy cell: an ndarray raises on bool() and NaN is a truthy float."""
    if value is None:
        return "[]"
    if isinstance(value, float):  # NaN
        return "[]"
    try:
        if isinstance(value, dict):
            return json.dumps({str(k): str(v) for k, v in value.items()})
        return json.dumps([str(v) for v in list(value)])
    except (TypeError, ValueError):
        return "[]"


def build_keyword_index(sources: list[str] | None = None,
                        limit: int | None = None) -> int:
    """Stream every normalized Parquet row group into SQLite, then build FTS5."""
    root = data_dir() / "normalized"
    if not root.is_dir():
        print(f"no normalized documents at {root} — run `just normalize` first")
        return 0

    path = db_path()
    if path.exists():
        path.unlink()
    conn = _connect(path)
    conn.executescript(SCHEMA)

    total = 0
    dirs = sorted(p for p in root.iterdir() if p.is_dir())
    if sources:
        dirs = [p for p in dirs if p.name in sources]

    for source_dir in dirs:
        files = sorted(source_dir.glob("*.parquet"))
        if not files:
            continue
        written = 0
        for file in files:
            parquet = pq.ParquetFile(file)
            for batch in parquet.iter_batches(batch_size=2000):
                rows = batch.to_pylist()
                if limit is not None:
                    remaining = limit - written
                    if remaining <= 0:
                        break
                    rows = rows[:remaining]
                payload = []
                for row in rows:
                    ts = row.get("timestamp")
                    payload.append((
                        row["doc_id"],
                        row.get("source_type") or source_dir.name,
                        row.get("title") or "",
                        row.get("body") or "",
                        int(ts.timestamp()) if hasattr(ts, "timestamp") else None,
                        row.get("thread_id"),
                        row.get("path") or "",
                        _to_json(row.get("author_refs")),
                        _to_json(row.get("mention_refs")),
                        _to_json(row.get("raw_metadata")),
                    ))
                conn.executemany(
                    "INSERT OR IGNORE INTO docs (doc_id, source_type, title, body, ts, "
                    "thread_id, path, author_refs, mention_refs, raw_metadata) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)", payload)
                written += len(payload)
            if limit is not None and written >= limit:
                break
        conn.commit()
        print(f"  {source_dir.name:<14} {written:>8,}")
        total += written

    print("building FTS5 index...")
    conn.executescript(FTS_SCHEMA)
    conn.execute("INSERT INTO docs_fts(docs_fts) VALUES('rebuild')")
    conn.commit()
    conn.execute("PRAGMA optimize")
    conn.close()
    return total


# ---------------------------------------------------------------------------
# vector index
# ---------------------------------------------------------------------------
def _pick_device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def build_vector_index(batch_size: int = 64) -> int:
    """Embed every document and write the matrix as a float16 memmap.

    Row order follows SQLite rowid exactly (rowid = index + 1). That alignment
    is the whole contract between the two indexes — a mismatch would silently
    return the wrong documents, so the row count is asserted, not assumed.
    """
    from sentence_transformers import SentenceTransformer

    cfg = settings()["index"]
    dim = int(cfg.get("embed_dim", 384))
    max_chars = int(cfg.get("max_chars_per_doc", 2000))

    conn = sqlite3.connect(db_path())
    count = conn.execute("SELECT count(*) FROM docs").fetchone()[0]
    if not count:
        print("no documents in the keyword index — nothing to embed")
        conn.close()
        return 0

    # Invalidate the manifest FIRST. It records the row count that maps vector
    # offsets back to documents, so a stale one against a freshly-rebuilt
    # keyword index silently resolves every hit to the wrong document. Removing
    # it makes search degrade to keyword-only for the duration of the rebuild,
    # which is a correct answer rather than a confidently wrong one. It is
    # rewritten at the end, once the matrix is complete and verified.
    meta_path().unlink(missing_ok=True)

    device = _pick_device()
    print(f"embedding {count:,} documents with {cfg['embed_model']} on {device}")
    model = SentenceTransformer(cfg["embed_model"], device=device)

    vectors = np.memmap(vectors_path(), dtype=np.float16, mode="w+", shape=(count, dim))
    cursor = conn.execute("SELECT rowid, title, body FROM docs ORDER BY rowid")

    written = 0
    with tqdm(total=count, desc="  embedding", unit="doc") as bar:
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            texts = [f"{title or ''}\n{(body or '')[:max_chars]}" for _, title, body in rows]
            emb = model.encode(texts, batch_size=batch_size, convert_to_numpy=True,
                               normalize_embeddings=True, show_progress_bar=False)
            start = rows[0][0] - 1
            vectors[start:start + len(rows)] = emb.astype(np.float16)
            written += len(rows)
            bar.update(len(rows))

    vectors.flush()
    del vectors
    conn.close()

    if written != count:
        raise SystemExit(f"embedded {written} of {count} documents — refusing to "
                         f"write a misaligned index")

    meta_path().write_text(json.dumps({
        "count": count, "dim": dim, "dtype": "float16",
        "model": cfg["embed_model"], "max_chars_per_doc": max_chars,
        "built_at": int(time.time()),
    }, indent=2))
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sources", nargs="*", default=None, help="limit to these sources")
    ap.add_argument("--limit", type=int, default=None, help="max docs per source (dev)")
    ap.add_argument("--skip-vectors", action="store_true",
                    help="keyword index only — fast, and needs no model download")
    ap.add_argument("--skip-keyword", action="store_true",
                    help="re-embed against the existing keyword index")
    ap.add_argument("--batch-size", type=int, default=64)
    args = ap.parse_args(argv)

    t0 = time.time()
    if not args.skip_keyword:
        print("building keyword index (SQLite FTS5)...")
        total = build_keyword_index(args.sources, args.limit)
        if not total:
            return 1
        print(f"  {total:,} documents indexed in {time.time() - t0:.1f}s\n")

    if not args.skip_vectors:
        t1 = time.time()
        n = build_vector_index(args.batch_size)
        print(f"  {n:,} vectors in {time.time() - t1:.1f}s")

    size = sum(f.stat().st_size for f in index_dir().iterdir() if f.is_file())
    print(f"\nindex ready at {index_dir()} ({size / 1e6:.0f} MB), "
          f"{time.time() - t0:.1f}s total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
