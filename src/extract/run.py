"""Driver for B1: normalized docs -> candidate mentions + relations on disk.

Reads NormalizedDoc rows (either the committed fixture or Track A's full
data/normalized/) and, per source, runs the matching extractor from sources.py.
Writes two Parquet files in the shapes frozen in schemas.py / CLAUDE.md §12:

    data/candidates/mentions.parquet
    data/candidates/relations.parquet

No LLM. Idempotent: rerunning overwrites the outputs from the same inputs.

CLI:
    python -m src.extract.run --input tests/fixtures/normalized_sample.parquet \
                              --out data/candidates
    python -m src.extract.run            # defaults to fixture -> data/candidates
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import pandas as pd

from src.common.schemas import Mention, NormalizedDoc, Relation
from src.extract.sources import get_extractor


def _row_to_doc(row: pd.Series) -> NormalizedDoc:
    def _list(v):
        if v is None:
            return []
        return list(v)

    md = row.get("raw_metadata")
    if md is None:
        md = {}
    elif not isinstance(md, dict):
        # normalizer stores it as a list of (key, value) tuples in Parquet
        md = {k: v for k, v in md}

    ts = row.get("timestamp")
    return NormalizedDoc(
        doc_id=row["doc_id"],
        source_type=row["source_type"],
        title=row.get("title") or "",
        body=row.get("body") or "",
        timestamp=None if pd.isna(ts) else ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
        author_refs=_list(row.get("author_refs")),
        mention_refs=_list(row.get("mention_refs")),
        thread_id=row.get("thread_id"),
        path=row.get("path") or "",
        raw_metadata=md,
    )


def extract_frame(df: pd.DataFrame) -> tuple[list[Mention], list[Relation]]:
    mentions: list[Mention] = []
    relations: list[Relation] = []
    per_source_docs: Counter = Counter()

    for source_type, group in df.groupby("source_type"):
        try:
            extractor = get_extractor(source_type)
        except KeyError:
            print(f"  [skip] no extractor for {source_type!r}")
            continue
        for _, row in group.iterrows():
            doc = _row_to_doc(row)
            res = extractor.extract_doc(doc)
            mentions.extend(res.mentions)
            relations.extend(res.relations)
            per_source_docs[source_type] += 1

    print("docs processed per source:", dict(per_source_docs))
    return mentions, relations


def _to_parquet(rows, columns, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        df = pd.DataFrame([{c: getattr(r, c) for c in columns} for r in rows])
    else:
        df = pd.DataFrame(columns=list(columns))
    df.to_parquet(path, index=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="B1 rule-based extraction")
    ap.add_argument("--input", default="tests/fixtures/normalized_sample.parquet",
                    help="a normalized parquet file, or a dir of them")
    ap.add_argument("--out", default="data/candidates",
                    help="output directory for mentions.parquet + relations.parquet")
    args = ap.parse_args(argv)

    in_path = Path(args.input)
    if in_path.is_dir():
        files = sorted(in_path.rglob("*.parquet"))
        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    else:
        df = pd.read_parquet(in_path)
    print(f"loaded {len(df)} normalized docs from {in_path}")

    mentions, relations = extract_frame(df)

    out = Path(args.out)
    _to_parquet(mentions, Mention.PARQUET_COLUMNS, out / "mentions.parquet")
    _to_parquet(relations, Relation.PARQUET_COLUMNS, out / "relations.parquet")

    # summary
    mtypes = Counter(m.entity_type for m in mentions)
    rtypes = Counter(r.rel_type for r in relations)
    print(f"\nwrote {len(mentions)} mentions -> {out / 'mentions.parquet'}")
    print("  by entity_type:", dict(mtypes.most_common()))
    print(f"wrote {len(relations)} relations -> {out / 'relations.parquet'}")
    print("  by rel_type:", dict(rtypes.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
