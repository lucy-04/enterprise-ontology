"""B7 — HERB entity-resolution spot-check (Salesforce/HERB, CLAUDE.md §3.2).

A small, separate stress test that our entity resolution actually works, on a
dataset built for exactly that. HERB gives, per product, a set of artifacts
(Slack, meeting transcripts, docs, PRs) plus ORACLE `team` / `customers` fields.

The rule (from the dataset card): you must INFER who is on a product's team from
the artifacts; you may NOT read the oracle `team`/`customers` fields as input.
We read `team` ONLY at the end, to score what we inferred.

Why this is a real ER test, not a lookup:
  - Slack tags each message with the author's employee id (eid) — easy signal.
  - But meeting transcripts refer to people by NAME only, and HERB has 530
    employees sharing just 98 names ("Hannah Taylor" is 10 different people).
    Resolving a transcript name to the right employee is genuine, ambiguous
    entity resolution — the "Sam / @soham / S. Ratnaparkhi" problem in miniature.
  - We disambiguate a shared name by CO-OCCURRENCE: of the candidate employees
    with that name, the one who also shows up (by eid) in the product's Slack is
    the intended person. This is the same context-based resolution our main
    pipeline uses, exercised on a clean, scorable benchmark.

Reports, per product and aggregated: team-recovery precision / recall / F1, and
how many ambiguous transcript names co-occurrence could resolve.

Run: python -m src.resolve.herb_check            (downloads HERB on first run)
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from src.common.config import REPO_ROOT

HERB_DIR = REPO_ROOT / "data" / "herb"
_EID_RE = re.compile(r"eid_[0-9a-f]+")
_SPEAKER_RE = re.compile(r"^([A-Z][a-z]+ [A-Z][a-z]+):", re.M)
_NAME_RE = re.compile(r"\b([A-Z][a-z]+ [A-Z][a-z]+)\b")


# --------------------------------------------------------------------------
# Data loading (downloads on first use; the file is gitignored)
# --------------------------------------------------------------------------
def ensure_herb(products: list[str] | None = None) -> None:
    """Download HERB metadata + product files into data/herb/ if missing."""
    from huggingface_hub import HfApi, hf_hub_download

    (HERB_DIR / "metadata").mkdir(parents=True, exist_ok=True)
    (HERB_DIR / "products").mkdir(parents=True, exist_ok=True)

    needed = ["metadata/employee.json", "metadata/salesforce_team.json"]
    if products is None:
        all_files = HfApi().list_repo_files("Salesforce/HERB", repo_type="dataset")
        needed += [f for f in all_files if f.startswith("products/")]
    else:
        needed += [f"products/{p}.json" for p in products]

    for rel in needed:
        dst = HERB_DIR / rel
        if dst.exists():
            continue
        src = hf_hub_download("Salesforce/HERB", rel, repo_type="dataset")
        dst.write_bytes(Path(src).read_bytes())
        print(f"  downloaded {rel}")


def load_employees() -> tuple[dict, dict]:
    emp = json.loads((HERB_DIR / "metadata" / "employee.json").read_text())
    name2eids: dict[str, list[str]] = defaultdict(list)
    for eid, rec in emp.items():
        name2eids[rec["name"]].append(eid)
    return emp, name2eids


def load_products() -> dict[str, dict]:
    out = {}
    for f in sorted((HERB_DIR / "products").glob("*.json")):
        out[f.stem] = json.loads(f.read_text())
    return out


# --------------------------------------------------------------------------
# Inference (NO oracle fields used here)
# --------------------------------------------------------------------------
def eid_evidence(product: dict) -> set[str]:
    """Employee ids observed directly in the artifacts (Slack authors + @eid + PRs)."""
    eids: set[str] = set()
    for m in product.get("slack", []):
        user = m.get("Message", {}).get("User", {})
        uid = user.get("userId", "")
        if uid.startswith("eid_"):
            eids.add(uid)
        eids.update(_EID_RE.findall(m.get("Message", {}).get("text", "")))
    for pr in product.get("prs", []):
        eids.update(_EID_RE.findall(json.dumps(pr)))
    return eids


def transcript_names(product: dict) -> set[str]:
    """People named in meeting transcripts (attendees + speakers)."""
    names: set[str] = set()
    for t in product.get("meeting_transcripts", []):
        tx = t.get("transcript", "")
        head = tx.split("Transcript", 1)[0]
        names.update(_NAME_RE.findall(head))     # attendees block
        names.update(_SPEAKER_RE.findall(tx))    # "Name: ..." speaker lines
    return names


def disambiguate(name: str, name2eids: dict, context_eids: set[str]) -> tuple[str | None, bool]:
    """Resolve a name to one eid. Returns (eid_or_None, was_ambiguous).

    Co-occurrence rule: among employees sharing this name, prefer the unique one
    who also appears (by eid) in this product's context. Unambiguous names
    resolve directly; names with several equally-plausible candidates are left
    unresolved rather than guessed.
    """
    cands = name2eids.get(name, [])
    ambiguous = len(cands) > 1
    if len(cands) == 1:
        return cands[0], False
    hits = [e for e in cands if e in context_eids]
    if len(hits) == 1:
        return hits[0], ambiguous
    return None, ambiguous


def infer_team(product: dict, name2eids: dict) -> tuple[set[str], dict]:
    """Infer team eids from artifacts only. Returns (inferred_eids, stats)."""
    eids = eid_evidence(product)
    names = transcript_names(product)
    resolved: set[str] = set()
    ambiguous_total = ambiguous_resolved = 0
    for n in names:
        eid, was_ambig = disambiguate(n, name2eids, eids)
        if was_ambig:
            ambiguous_total += 1
        if eid:
            resolved.add(eid)
            if was_ambig:
                ambiguous_resolved += 1
    stats = {
        "slack_eids": len(eids),
        "transcript_names": len(names),
        "ambiguous_names": ambiguous_total,
        "ambiguous_resolved": ambiguous_resolved,
    }
    return eids | resolved, stats


# --------------------------------------------------------------------------
# Scoring (oracle read HERE ONLY)
# --------------------------------------------------------------------------
def prf(inferred: set[str], oracle: set[str]) -> tuple[float, float, float]:
    if not inferred or not oracle:
        return 0.0, 0.0, 0.0
    tp = len(inferred & oracle)
    precision = tp / len(inferred)
    recall = tp / len(oracle)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def run(limit: int | None = None) -> dict:
    _, name2eids = load_employees()
    products = load_products()
    if limit:
        products = dict(list(products.items())[:limit])

    rows = []
    agg_p = agg_r = agg_f = 0.0
    amb_total = amb_res = 0
    for name, prod in products.items():
        oracle = set(prod.get("team", []))
        if not oracle:
            continue
        inferred, stats = infer_team(prod, name2eids)
        p, r, f = prf(inferred, oracle)
        agg_p += p; agg_r += r; agg_f += f
        amb_total += stats["ambiguous_names"]; amb_res += stats["ambiguous_resolved"]
        rows.append((name, len(oracle), p, r, f, stats))

    n = len(rows)
    print(f"\nHERB team-recovery from artifacts only (no oracle fields as input) — {n} products\n")
    print(f"{'product':<22}{'team':>5}{'prec':>7}{'rec':>7}{'f1':>7}   ambiguous-names")
    for name, tsize, p, r, f, st in rows:
        print(f"{name:<22}{tsize:>5}{p:>7.2f}{r:>7.2f}{f:>7.2f}   "
              f"{st['ambiguous_resolved']}/{st['ambiguous_names']} resolved")
    if n:
        print("-" * 64)
        print(f"{'MEAN':<22}{'':>5}{agg_p/n:>7.2f}{agg_r/n:>7.2f}{agg_f/n:>7.2f}   "
              f"{amb_res}/{amb_total} resolved ({(amb_res/amb_total if amb_total else 0):.0%})")
        print(f"\nHeadline: recovered {agg_r/n:.0%} of true team membership (mean recall) "
              f"at {agg_p/n:.0%} precision, inferring only from artifacts; "
              f"co-occurrence disambiguated {amb_res}/{amb_total} "
              f"({(amb_res/amb_total if amb_total else 0):.0%}) of ambiguous shared-name references.")
    return {"products": n, "mean_recall": agg_r / n if n else 0,
            "mean_precision": agg_p / n if n else 0, "mean_f1": agg_f / n if n else 0}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="B7 HERB entity-resolution spot-check")
    ap.add_argument("--limit", type=int, default=None, help="only the first N products")
    ap.add_argument("--no-download", action="store_true", help="skip the HERB download step")
    args = ap.parse_args(argv)
    if not args.no_download:
        ensure_herb()
    run(limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
