"""Download the EnterpriseRAG-Bench corpus and questions.  `just fetch-data`

Release v1.0.0 is ~1.26 GB zipped for all 500K documents, plus questions.jsonl
(500 questions) and extra_questions.jsonl (100, excluded from the leaderboard).

Resumable: an existing complete file is skipped, and a partial download is
continued rather than restarted.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

import httpx
from tqdm import tqdm

from src.common.config import data_dir

RELEASE = "https://github.com/onyx-dot-app/EnterpriseRAG-Bench/releases/download/v1.0.0"
API_LATEST = "https://api.github.com/repos/onyx-dot-app/EnterpriseRAG-Bench/releases/latest"

QUESTION_FILES = ("questions.jsonl", "extra_questions.jsonl")


def download(url: str, dest: Path) -> Path:
    """Stream a URL to disk, resuming a partial file if present."""
    dest.parent.mkdir(parents=True, exist_ok=True)

    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as head:
        total = int(head.headers.get("content-length", 0))

    if dest.exists() and total and dest.stat().st_size == total:
        print(f"  skip  {dest.name} (already complete)")
        return dest

    have = dest.stat().st_size if dest.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    mode = "ab" if have else "wb"

    with httpx.stream("GET", url, headers=headers, follow_redirects=True,
                      timeout=60.0) as resp:
        resp.raise_for_status()
        with open(dest, mode) as fh, tqdm(
            total=total or None, initial=have, unit="B", unit_scale=True,
            desc=f"  {dest.name}",
        ) as bar:
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                fh.write(chunk)
                bar.update(len(chunk))
    return dest


def slice_names() -> list[str]:
    """Every per-source slice asset in the latest release."""
    resp = httpx.get(API_LATEST, timeout=30.0)
    resp.raise_for_status()
    return [
        a["name"] for a in resp.json().get("assets", [])
        if "_slice_" in a["name"] and a["name"].endswith(".zip")
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--slices", type=int, default=None,
        help="download only N slices per source instead of the full corpus "
             "(much faster for iteration)",
    )
    ap.add_argument("--no-extract", action="store_true", help="download only, don't unzip")
    args = ap.parse_args(argv)

    raw = data_dir() / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    print("questions:")
    for name in QUESTION_FILES:
        download(f"{RELEASE}/{name}", raw / name)

    zips: list[Path] = []
    if args.slices is None:
        print("\ncorpus (full, ~1.26 GB):")
        zips.append(download(f"{RELEASE}/all_documents.zip", raw / "all_documents.zip"))
    else:
        print(f"\ncorpus ({args.slices} slice(s) per source):")
        by_source: dict[str, list[str]] = {}
        for name in sorted(slice_names()):
            source = name.rsplit("_slice_", 1)[0]
            by_source.setdefault(source, []).append(name)
        for source, names in by_source.items():
            for name in names[: args.slices]:
                zips.append(download(f"{RELEASE}/{name}", raw / name))

    if args.no_extract:
        return 0

    print("\nextracting:")
    docs = raw / "documents"
    docs.mkdir(parents=True, exist_ok=True)
    for zpath in zips:
        with zipfile.ZipFile(zpath) as zf:
            members = zf.namelist()
            for member in tqdm(members, desc=f"  {zpath.name}", unit="file"):
                zf.extract(member, docs)

    counts: dict[str, int] = {}
    for path in docs.rglob("*.txt"):
        source = path.parent.name
        counts[source] = counts.get(source, 0) + 1

    print("\ndocuments per source:")
    for source, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {source:16} {n:>7,}")
    print(f"  {'TOTAL':16} {sum(counts.values()):>7,}")
    print("\nCompare against CLAUDE.md §3.1 and record the actuals in progress/track-a.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
