"""raw .txt -> data/normalized/{source}/part-*.parquet   `just normalize`

Track A task A2. Turns nine differently-shaped sources into the single
NormalizedDoc contract (src/common/schemas.py, CLAUDE.md §12).

Deliberately does NOT resolve anything. author_refs and mention_refs hold raw
surface forms exactly as written — "sam", "Sam Chen", "Support (Aisha)" all
survive verbatim. Deciding those are one person is Track B's job (B3), and
throwing away a surface form here would destroy the evidence they need.

Per-source parsing follows the measured formats in CLAUDE.md §7.4. Read that
before changing anything here.

Memory: streams one source at a time, writes in row groups, and never holds the
corpus in memory — the dev machine has 8 GB.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from tqdm import tqdm

from src.common.config import data_dir, env_int, settings
from src.common.schemas import SOURCE_TYPES

# --------------------------------------------------------------------------
# Patterns. Grouped by which source they serve so §7.4 stays traceable here.
# --------------------------------------------------------------------------

# Filename: dsid_<32hex>__<semantic-slug>.txt   (note the DOUBLE underscore)
DOC_ID_RE = re.compile(r"^(dsid_[0-9a-f]{32})__(.+)\.txt$", re.IGNORECASE)

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# gmail: real RFC-style headers
HEADER_RE = re.compile(r"^(From|To|Cc|Bcc|Date|Subject):\s*(.*)$", re.MULTILINE)
# "Name <addr@x>" or bare address
ADDR_RE = re.compile(r"([^<,;]+?)\s*<([^>]+)>|([\w.+-]+@[\w-]+\.[\w.-]+)")

# slack: speaker lines. MEASURED: only ~2/20 docs use "handle (team):"; the
# other ~18 use a bare "speaker:" in many shapes — `jin:`, `Maria R.:`,
# `lena-sales:`, `maria.s:`, `Alex K:`, `incident-bot:`, `oncall-ryan:`.
# The optional (team) group captures the minority form without missing the rest.
SLACK_SPEAKER_RE = re.compile(
    r"^([A-Za-z][A-Za-z0-9._\-]{0,24}(?:\s+[A-Z][A-Za-z.]{0,14})?)"  # name / handle
    r"(?:\s*\(([^)]{1,40})\))?"                                       # optional (team)
    r":\s",
    re.MULTILINE,
)
# @mentions appear inside slack messages: "@sre-jaya can you grab gateway logs?"
AT_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z][\w.\-]{1,30})")

# Lines that look like "Word:" but are prose labels, not speakers. Without this
# filter, "Requirements:", "Impact:", "Summary:" all become people.
NOT_A_SPEAKER = {
    "http", "https", "note", "notes", "summary", "impact", "timeline", "scope",
    "purpose", "goal", "goals", "problem", "requirements", "required", "example",
    "examples", "steps", "repro", "result", "results", "status", "owner", "owners",
    "next", "action", "actions", "context", "background", "solution", "mitigation",
    "root", "cause", "detail", "details", "issue", "update", "updates", "warning",
    "error", "errors", "response", "request", "logic", "design", "testing", "tests",
    "rollout", "migration", "motivation", "definitions", "objective", "overview",
    "principles", "process", "sla", "eta", "tldr", "tl;dr", "faq", "q", "a",
    "auto-summary", "post-meeting notes", "attendees", "agenda", "decisions",
    "risks", "blockers", "asks", "ask", "priority", "severity", "environment",
}

# jira / fireflies / hubspot: the shared "X (Y)" family (§7.4)
PAREN_SPEAKER_RE = re.compile(r"^([A-Z][A-Za-z.\-' ]{1,40}?)\s*\(([^)]{1,40})\):", re.MULTILINE)
FIREFLIES_ACTION_RE = re.compile(r"^([A-Z][\w.\-' ]{1,40}?)\s*\(([^)]{1,40})\)\s+to\s+", re.MULTILINE)
INLINE_ROLE_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*\((AE|SE|PM|EM|SRE|CSM|AM)\)")

# Cross-source join keys — the multi-hop goldmine (§7.4).
TICKET_RE = re.compile(r"\b((?:SUP|TRACK|OPS|DOC|ENG|INFRA|SEC)-\d+)\b")
PR_RE = re.compile(r"\bPR\s*#(\d+)\b", re.IGNORECASE)
FIREFLIES_ID_RE = re.compile(r"\b(ff_\d{8}_[0-9a-f]+)\b")

ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2})(?::\d{2})?)?Z?\b")

MAX_REFS = 200  # cap per doc; a 5,000-line slack dump shouldn't blow up a row


def _dedupe(seq: list[str], limit: int = MAX_REFS) -> list[str]:
    """Order-preserving dedupe. Order matters — first speaker is usually the author."""
    seen: dict[str, None] = {}
    for item in seq:
        item = item.strip()
        if item and item not in seen:
            seen[item] = None
            if len(seen) >= limit:
                break
    return list(seen)


def _first_timestamp(text: str) -> datetime | None:
    m = ISO_DATE_RE.search(text)
    if not m:
        return None
    try:
        return datetime.fromisoformat(f"{m.group(1)}T{m.group(2) or '00:00'}")
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Per-source parsers. Each returns (author_refs, mention_refs, thread_id,
# timestamp, extra_metadata). See CLAUDE.md §7.4 for why each looks like this.
# --------------------------------------------------------------------------

def _parse_gmail(title: str, body: str) -> tuple[list[str], list[str], str | None,
                                                 datetime | None, dict]:
    """Real RFC headers — the highest-confidence source for Person nodes."""
    authors: list[str] = []
    mentions: list[str] = []
    meta: dict[str, str] = {}
    ts: datetime | None = None

    for field, value in HEADER_RE.findall(body):
        meta.setdefault(field.lower(), value)
        if field == "Date" and ts is None:
            try:
                ts = parsedate_to_datetime(value).replace(tzinfo=None)
            except (TypeError, ValueError):
                ts = None
        if field in ("From", "To", "Cc", "Bcc"):
            target = authors if field == "From" else mentions
            for name, addr, bare in ADDR_RE.findall(value):
                if name:
                    target.append(name.strip())
                if addr:
                    target.append(addr.strip())
                if bare:
                    target.append(bare.strip())

    mentions.extend(EMAIL_RE.findall(body))
    # Threads reply into one subject; use it to group.
    thread = meta.get("subject")
    return _dedupe(authors), _dedupe(mentions), thread, ts, meta


def _is_speaker(token: str, counts: dict[str, int]) -> bool:
    """Keep real speakers, drop prose labels like "Requirements:".

    A token qualifies if it is not a known label AND either recurs in the
    document (speakers talk more than once) or looks like a handle rather than
    an English word (contains . - _ or a digit, or is a two-word Name form).
    """
    low = token.lower().strip()
    if low in NOT_A_SPEAKER or len(low) < 2:
        return False
    if counts.get(token, 0) >= 2:
        return True
    if any(c in token for c in "._-") or any(c.isdigit() for c in token):
        return True
    return " " in token.strip()  # "Maria R." / "Alex K"


def _parse_slack(title: str, body: str) -> tuple[list[str], list[str], str | None,
                                                 datetime | None, dict]:
    """Speaker lines under a channel name on line 1.

    Measured: ~2/20 docs use `handle (team):`, ~18/20 use a bare `speaker:`.
    Both are handled, and prose labels are filtered out by _is_speaker.
    """
    raw = SLACK_SPEAKER_RE.findall(body)
    counts: dict[str, int] = {}
    for name, _ in raw:
        counts[name] = counts.get(name, 0) + 1

    handles = [n.strip() for n, _ in raw if _is_speaker(n, counts)]
    teams = [t.strip() for _, t in raw if t]
    ats = AT_MENTION_RE.findall(body)

    meta = {"channel": title, "teams": ",".join(_dedupe(teams, 40))}
    return _dedupe(handles), _dedupe(handles + ats + EMAIL_RE.findall(body)), \
        title, _first_timestamp(body), meta


def _parse_paren_family(title: str, body: str) -> tuple[list[str], list[str], str | None,
                                                        datetime | None, dict]:
    """jira / fireflies / hubspot — the shared `X (Y)` shape (§7.4).

    Both orderings occur and both matter: `Support (Aisha):` and
    `Aisha Patel (Support):` are the same person, which is exactly the
    entity-resolution case Track B has to solve. Keep both surface forms.
    """
    people: list[str] = []
    roles: list[str] = []

    def add(left: str, right: str) -> None:
        left, right = left.strip(), right.strip()
        # Without this, "Auto-summary (auto-generated, may be partial):" and
        # "Post-meeting notes (auto):" become people.
        if left.lower() in NOT_A_SPEAKER or len(left) < 2:
            return
        people.append(left)
        if right.lower() not in NOT_A_SPEAKER:
            roles.append(right)

    for left, right in PAREN_SPEAKER_RE.findall(body):
        add(left, right)
    for left, right in FIREFLIES_ACTION_RE.findall(body):
        add(left, right)
    for name, role in INLINE_ROLE_RE.findall(body):
        add(name, role)

    meta = {"roles": ",".join(_dedupe(roles, 40))}
    return _dedupe(people[:1]), _dedupe(people + roles + EMAIL_RE.findall(body)), \
        None, _first_timestamp(body), meta


def _parse_generic(title: str, body: str) -> tuple[list[str], list[str], str | None,
                                                   datetime | None, dict]:
    """confluence / google_drive / github / linear — prose, little structure.

    Layer 1 sources (§7.4). Pull what's cheap and don't pretend to more.
    """
    mentions = EMAIL_RE.findall(body)
    for name, role in INLINE_ROLE_RE.findall(body):
        mentions.extend([name.strip(), role.strip()])
    return [], _dedupe(mentions), None, _first_timestamp(body), {}


PARSERS = {
    "gmail": _parse_gmail,
    "slack": _parse_slack,
    "jira": _parse_paren_family,
    "fireflies": _parse_paren_family,
    "hubspot": _parse_paren_family,
    "confluence": _parse_generic,
    "google_drive": _parse_generic,
    "github": _parse_generic,
    "linear": _parse_generic,
}


def parse_file(path_and_source: tuple[str, str]) -> dict | None:
    """Parse one .txt into a NormalizedDoc-shaped dict. Never raises."""
    path_str, source = path_and_source
    path = Path(path_str)
    try:
        m = DOC_ID_RE.match(path.name)
        if not m:
            return None
        doc_id, slug = m.group(1), m.group(2)

        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n", 1)
        title = lines[0].strip()
        body = lines[1] if len(lines) > 1 else ""

        parser = PARSERS.get(source, _parse_generic)
        authors, mentions, thread_id, ts, meta = parser(title, body)

        # Cross-source join keys, from every source (§7.4). These become the
        # deterministic multi-hop edges, so collect them everywhere.
        refs = TICKET_RE.findall(text) + TICKET_RE.findall(slug)
        refs += [f"PR#{n}" for n in PR_RE.findall(text)]
        refs += FIREFLIES_ID_RE.findall(text)

        meta = {**meta, "slug": slug, "cross_refs": ",".join(_dedupe(refs, 60))}

        return {
            "doc_id": doc_id,
            "source_type": source,
            "title": title,
            "body": body,
            "timestamp": ts,
            "author_refs": authors,
            "mention_refs": mentions,
            "thread_id": thread_id,
            "path": str(path),
            "raw_metadata": {k: str(v) for k, v in meta.items()},
        }
    except Exception:  # noqa: BLE001 - one bad file must not kill a 500K run
        return None


SCHEMA = pa.schema([
    ("doc_id", pa.string()),
    ("source_type", pa.string()),
    ("title", pa.string()),
    ("body", pa.string()),
    ("timestamp", pa.timestamp("us")),
    ("author_refs", pa.list_(pa.string())),
    ("mention_refs", pa.list_(pa.string())),
    ("thread_id", pa.string()),
    ("path", pa.string()),
    ("raw_metadata", pa.map_(pa.string(), pa.string())),
])


def normalize_source(source: str, files: list[Path], out_dir: Path,
                     workers: int, row_group: int) -> int:
    """Parse one source into Parquet, streaming in row groups."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "part-0000.parquet"

    written = 0
    batch: list[dict] = []
    writer: pq.ParquetWriter | None = None
    tasks = [(str(f), source) for f in files]

    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for rec in tqdm(pool.map(parse_file, tasks, chunksize=64),
                            total=len(tasks), desc=f"  {source:13}", unit="doc"):
                if rec is None:
                    continue
                batch.append(rec)
                if len(batch) >= row_group:
                    table = pa.Table.from_pylist(batch, schema=SCHEMA)
                    if writer is None:
                        writer = pq.ParquetWriter(out_path, SCHEMA, compression="zstd")
                    writer.write_table(table)
                    written += len(batch)
                    batch = []
        if batch:
            table = pa.Table.from_pylist(batch, schema=SCHEMA)
            if writer is None:
                writer = pq.ParquetWriter(out_path, SCHEMA, compression="zstd")
            writer.write_table(table)
            written += len(batch)
    finally:
        if writer is not None:
            writer.close()
    return written


def find_sources(root: Path) -> dict[str, list[Path]]:
    """Map source name -> its .txt files, wherever the extractor put them."""
    found: dict[str, list[Path]] = {}
    for source in SOURCE_TYPES:
        files: list[Path] = []
        for candidate in (root / source, root / "documents" / source):
            if candidate.is_dir():
                files.extend(candidate.glob("*.txt"))
        if files:
            found[source] = sorted(files)
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default=None,
                    help="directory of source subdirs (default: $DATA_DIR/raw)")
    ap.add_argument("--output", default=None,
                    help="output root (default: $DATA_DIR/normalized)")
    ap.add_argument("--sources", nargs="*", default=None, help="limit to these sources")
    ap.add_argument("--limit", type=int, default=None, help="max docs per source (dev)")
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args(argv)

    root = Path(args.input) if args.input else data_dir() / "raw"
    out_root = Path(args.output) if args.output else data_dir() / "normalized"
    workers = args.workers or env_int("NORMALIZE_WORKERS", max(1, (os.cpu_count() or 4) - 2))
    row_group = env_int("PARQUET_ROW_GROUP", settings()["index"].get("row_group", 20000))

    by_source = find_sources(root)
    if args.sources:
        by_source = {k: v for k, v in by_source.items() if k in args.sources}
    if not by_source:
        print(f"no source directories found under {root}")
        print("run `just fetch-data` first, or pass --input")
        return 1

    print(f"normalizing from {root}\n  workers={workers} row_group={row_group}\n")
    total = 0
    for source, files in by_source.items():
        if args.limit:
            files = files[: args.limit]
        total += normalize_source(source, files, out_root / source, workers, row_group)

    print(f"\nwrote {total:,} documents to {out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
