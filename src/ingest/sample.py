"""Sample the normalized corpus down to a graph-sized subset.  `just sample`

Layer 1 indexes all 511,961 documents, because keyword and vector search cost
nothing per document. Layer 2 cannot: the entity-resolution stage materialises
its working set in memory, which puts a ceiling of a few thousand documents on
an 8GB machine (measured — see README, "Honest limitations").

So the graph is built over a sample, and this is what draws it. Two properties
matter, and neither is automatic:

  * **Proportional.** The corpus is 56% Slack and 1% Confluence. Sampling
    uniformly across sources would build a graph out of a document mix that
    does not exist, and the entity-resolution behaviour we measure on it would
    not carry over to the full corpus.

  * **Deterministic.** A fixed seed means the same N always produces the same
    sample, so a result measured on it can be reproduced, and two runs at
    different scales are comparable rather than merely similar.

Every source keeps at least one document, so a small sample never silently
drops a whole source — losing Fireflies entirely would remove a whole class of
relation from the ontology without any error being raised.

Usage:
    python -m src.ingest.sample --n 2000
    python -m src.ingest.sample --n 5000 --out data/sample/5000.parquet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.common.config import data_dir

DEFAULT_SEED = 20260820


def source_counts(root: Path) -> dict[str, int]:
    """Rows per source, read from Parquet metadata rather than by loading."""
    counts: dict[str, int] = {}
    for source_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        total = sum(pq.ParquetFile(f).metadata.num_rows
                    for f in sorted(source_dir.glob("*.parquet")))
        if total:
            counts[source_dir.name] = total
    return counts


def quota(counts: dict[str, int], n: int) -> dict[str, int]:
    """How many documents to take from each source.

    Largest-remainder allocation, with a floor of one per source. Plain rounding
    would drop the smallest sources to zero at small n and would not sum to n.
    """
    total = sum(counts.values())
    exact = {s: n * c / total for s, c in counts.items()}
    take = {s: max(1, int(v)) for s, v in exact.items()}

    # Hand out whatever the flooring lost, to the sources with the largest
    # fractional remainder — the standard largest-remainder method.
    shortfall = n - sum(take.values())
    if shortfall > 0:
        order = sorted(exact, key=lambda s: exact[s] - int(exact[s]), reverse=True)
        for i in range(shortfall):
            take[order[i % len(order)]] += 1
    elif shortfall < 0:
        # n is smaller than the number of sources; trim the biggest first, but
        # never below the one-document floor.
        order = sorted(take, key=lambda s: take[s], reverse=True)
        for s in order:
            if shortfall == 0:
                break
            if take[s] > 1:
                take[s] -= 1
                shortfall += 1
    return {s: min(v, counts[s]) for s, v in take.items()}


def sample(root: Path, n: int, seed: int = DEFAULT_SEED) -> pa.Table:
    """Take a proportional, deterministic sample across all sources."""
    import random

    counts = source_counts(root)
    if not counts:
        raise SystemExit(f"no normalized documents under {root} — run `just normalize`")
    plan = quota(counts, n)

    tables: list[pa.Table] = []
    for source, want in sorted(plan.items()):
        files = sorted((root / source).glob("*.parquet"))
        table = pq.read_table(files) if len(files) > 1 else pq.read_table(files[0])
        have = table.num_rows
        # Sample indices rather than shuffling the table: at 285K Slack rows a
        # shuffle copies the whole column set for the sake of a few hundred rows.
        rng = random.Random(f"{seed}:{source}")
        picked = sorted(rng.sample(range(have), min(want, have)))
        tables.append(table.take(picked))
        print(f"  {source:<14} {len(picked):>6,} of {have:>8,}")

    out = pa.concat_tables(tables)
    print(f"  {'TOTAL':<14} {out.num_rows:>6,}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=2000, help="documents to sample")
    ap.add_argument("--root", type=Path, default=data_dir() / "normalized")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args(argv)

    out = args.out or (data_dir() / "sample" / f"{args.n}.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"sampling {args.n:,} documents (seed {args.seed})")
    table = sample(args.root, args.n, args.seed)
    pq.write_table(table, out)
    print(f"\nwrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
    print(f"next: just extract --input {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
