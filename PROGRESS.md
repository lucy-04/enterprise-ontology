# Progress log

Running record of what has been built, what broke, and what was decided. **Append-only below the status block** — never rewrite history, because the value of this file is that a future session (or a teammate) can see what was already tried and failed.

**If you are an agent picking this up cold:** read the Status block, then the most recent 2–3 session entries, then §Open issues. Don't re-derive decisions listed in §Decisions — they were made with the project owner.

---

## Status — updated 2026-08-17 23:00 IST

| | |
|---|---|
| **Phase** | Pre-build. Planning and docs done; no code written yet. |
| **Deadline** | 2026-08-20 23:59 PT — **~3 days left** |
| **Next action** | **A0** — repo scaffold (`src/`, `pyproject.toml`, `justfile`, `.env.example`) then the HydraDB build spike |
| **Blocked on** | nothing |
| **Track A (Lakshay, infra)** | not started |
| **Track B (Shaurya, AI)** | not started — needs `ontology/ontology.yaml` (B0) tonight |
| **HydraDB** | ❓ not yet built or connected — this is the single biggest unknown |
| **Corpus** | ❓ not yet downloaded |
| **Eval score** | — no run yet |

### What must be true by end of tonight
1. `just db-check` passes against a running HydraDB node — or the Neo4j fallback is explicitly declared here.
2. The §12 contracts in `CLAUDE.md` are agreed by both people, so the two tracks stop blocking each other.
3. `ontology/ontology.yaml` v1 exists (Track B).

---

## Decisions

Settled with the project owner. Don't reopen these without asking.

| Date | Decision | Reasoning |
|---|---|---|
| 2026-08-17 | **Two-layer architecture** — full-corpus search index (Layer 1) + HydraDB ontology graph (Layer 2), with a router per question | Lookup questions and reasoning questions need different machinery; neither layer alone covers the 500-question mix. `CLAUDE.md` §7.1 |
| 2026-08-17 | **Rule-based extraction is primary; LLM only for ambiguity** | Free-tier LLM access (~1K–15K req/day) cannot read 500K docs. The corpus is synthetic and structured, so parsers recover most entities for free. Matches the brief's own "extraction is the easy part" framing. `CLAUDE.md` §7.2 |
| 2026-08-17 | **Full ~500K corpus**, not a sample | Rule-based extraction is free, so sampling buys nothing. |
| 2026-08-17 | **No Docker** | Docker Desktop reserves 2–3 GB on an 8 GB machine for zero benefit; everything runs natively. `SETUP.md` §0 |
| 2026-08-17 | **SQLite FTS5 + local sentence-transformers** for Layer 1 | Free, no extra services, fine at 500K rows. |
| 2026-08-17 | **Parquet on disk as the inter-track interface** | Lets Track A and Track B hand work off without a shared running service. `CLAUDE.md` §12 |
| 2026-08-17 | **Web UI with node-link graph view** | Product completeness is a judging criterion, and the UI *is* the demo video. |
| 2026-08-17 | Work split: **Lakshay = infra (A0–A8), Shaurya = AI (B0–B7)** | Plays to each person's strengths; they meet only at the §12 contracts. |

---

## Session log

### Session 1 — 2026-08-17, ~22:20–23:00 IST

**Goal:** turn the hackathon brief into an executable two-person plan, and set up tracking docs.

**Done:**
- Explained the proposed architecture to the project owner from first principles (no prior graph/RAG background assumed) and got the two-layer design approved.
- Established the free-tier LLM constraint and redesigned extraction around it — this was the biggest change from the original plan in `CLAUDE.md` §7, which had assumed batched LLM calls over every document.
- Wrote the full build plan to `~/.claude/plans/this-is-the-hackaton-ethereal-church.md`.
- **Rewrote `CLAUDE.md`:** replaced §7 (architecture) and §8 (open decisions → settled decisions); added §11 team split, §12 inter-track contracts, §13 schedule; fixed the §10 owner-name inconsistency.
- Surveyed the dev machine (see Environment findings below).
- Wrote `SETUP.md` and this file.

**Environment findings (2026-08-17):**

| Finding | Implication |
|---|---|
| **8 GB RAM, Apple M1, 8 cores** | ⚠️ **The binding constraint on this project.** Nothing may load the full corpus into memory. Every stage must stream. Embedding batches capped at 64. Splink must use its disk-spilling DuckDB backend. Rust builds may need `CARGO_BUILD_JOBS=4` to avoid OOM. |
| 93 GB free disk | Enough for corpus + Parquet + indexes + Rust build artifacts, but not enormous. Watch it. |
| System Python is **3.9.6** | Too old for Splink/modern pyarrow. Project pins 3.12 via `uv` — never use system Python. |
| Rust **1.97.0** | Above HydraDB's 1.91 minimum ✅ |
| `uv` 0.11.17, Node 24.16.0, Homebrew 6.0.1 | Present ✅ |
| `just` **not installed** | `brew install just` — needed for the task runner |
| Docker **not installed**, daemon not running | Confirmed the no-Docker decision rather than working around it |
| All 3 existing git commits dated **2026-08-17** | ✅ Clears the hackathon's "no commits before Aug 12" rule |

**Issues hit:** none yet — no code has run.

**Next session starts here:** A0 — scaffold `src/{ingest,index,graph,api,ui}`, `pyproject.toml` (uv, Python 3.12), `justfile`, `.env.example`; then `brew install just libcypher-parser suite-sparse`, clone and build HydraDB, and get `just db-check` green.

---

## Open issues

Problems currently unresolved. Move to §Resolved issues when fixed, with the fix recorded.

| # | Issue | Status | Notes |
|---|---|---|---|
| — | _none yet_ | | |

## Resolved issues

Keep these even after fixing — a future session hitting the same symptom should find the answer here rather than rediscovering it.

| # | Issue | Cause | Fix | Date |
|---|---|---|---|---|
| — | _none yet_ | | | |

---

## Risks being watched

| Risk | Likelihood | If it happens |
|---|---|---|
| **HydraDB won't build on macOS/M1** (Rust + SuiteSparse GraphBLAS) | Medium | Fall back to local Neo4j for development only (same Bolt driver, connection-string swap), keep fixing HydraDB in parallel. Final numbers **must** come from HydraDB — it's a scored requirement. Declare the fallback in the Status block if used. |
| **HydraDB path procedures too slow at real graph scale** | Medium | Verified fine on 3 nodes proves nothing. Re-test `algo.SSpaths` after the real load. If slow, cap `maxLen` at 2 and pre-filter candidate endpoints via Layer 1. |
| **8 GB RAM insufficient for the full 500K pipeline** | Medium | Drop to per-source slices (`just fetch-data --slices N`) and document the reduced corpus honestly in the README. Do not silently ship partial coverage. |
| **Free-tier LLM quota exhausted at a bad moment** | Medium | All LLM stages are disk-cached and resumable. Keep a second provider configured in `.env` to switch to. Remember the bench's own scorer also burns quota. |
| **UI left to the last hours** | Medium | The UI is the demo video. If Aug 20 morning arrives without it, cut features from it rather than skipping it. |
| **Running out of time before eval** | High | Cut order is fixed in `CLAUDE.md` §13. Follow it rather than improvising under pressure. |

---

## How to update this file

At the end of any meaningful work session, or whenever something breaks:

1. Update the **Status** block at the top (it should always describe *right now*).
2. Append a **Session log** entry: goal → done → issues hit → where the next session starts.
3. Log any new problem in **Open issues**; move it to **Resolved issues** with its cause and fix once solved.
4. Add a row to **Decisions** if a choice was made that a future session shouldn't reopen.

Record failures and dead ends too. "We tried X, it didn't work because Y" is the most valuable thing in this file — it's what stops the next session burning an hour repeating it.
