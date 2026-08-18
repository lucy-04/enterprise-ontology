# Progress — where this project actually is

`CLAUDE.md` says what we intend to build. **This file says where we actually are.** When they disagree, this file is right.

**If you are an agent or a person picking this up cold:** read the Status block, then your track's log, then Risks. Don't reopen anything in Decisions — those were settled with the project owner.

> ⚠️ **Keep edits to this file small** — both people touch it. Detailed session notes go in your own track log, never here:
> - **`progress/track-a.md`** — Track A (infra) session log and issues
> - **`progress/track-b.md`** — Track B (AI) session log and issues

---

## Status — 2026-08-18 05:10 IST

| | |
|---|---|
| **Phase** | A0 scaffold done and committed. **Track B is unblocked and can start now.** |
| **Deadline** | 2026-08-20 23:59 PT — **~3 days left** |
| **Track A** (Lakshay, infra) | A0 ✅ A2 ✅. HydraDB verified. Next: A3 (FTS5 + vector index). |
| **Track B** (Shaurya, AI) | ▶️ **Ready to start.** Contracts are committed; see `progress/track-b.md` for the first-session checklist. |
| **HydraDB** | ✅ **WORKING & VERIFIED.** `just db-check` green: HTTP + Bolt round-trip, `algo.SPpaths` 2-hop path, variable-length `*1..3`. Biggest risk is retired. |
| **Corpus** | ⏸️ full download paused at 209/1256 MB (resumable via `just fetch-data`). One slice per source (~5K docs each) is local, plus `questions.jsonl`. |
| **Eval score** | — no run yet |
| **Blocked on** | nothing (downloads are running unattended) |

### Must be true by end of tonight
1. ✅ Interface stubs committed so neither track blocks the other (`CLAUDE.md` §14.3) — `src/common/schemas.py`, `src/graph/client.py`.
2. ✅ Dependencies agreed and committed once, by Track A only (`CLAUDE.md` §14.2).
3. ✅ `just db-check` passes against a running HydraDB node. **No fallback needed — we are on real HydraDB.**
4. ⬜ `ontology/ontology.yaml` v1 exists (Track B).

---

## Decisions

Settled with the project owner. Don't reopen without asking.

| Date | Decision | Reasoning |
|---|---|---|
| 2026-08-17 | **Two-layer architecture** — full-corpus search index (Layer 1) + HydraDB ontology graph (Layer 2), router picks per question | Lookup questions and reasoning questions need different machinery; neither layer alone covers the 500-question mix. `CLAUDE.md` §7.1 |
| 2026-08-17 | **Rule-based extraction is primary; LLM only for ambiguity** | Free-tier LLM access (~1K–15K req/day) cannot read 500K docs. The corpus is synthetic and structured, so parsers recover most entities for free. Matches the brief's own "extraction is the easy part" framing. `CLAUDE.md` §7.2 |
| 2026-08-17 | **Full ~500K corpus**, not a sample | Rule-based extraction is free, so sampling buys nothing. Revisit only if 8 GB RAM forces it. |
| 2026-08-17 | **No Docker** | Docker Desktop reserves 2–3 GB on an 8 GB machine for zero benefit; everything runs natively. `SETUP.md` §0 |
| 2026-08-17 | **SQLite FTS5 + local sentence-transformers** for Layer 1 | Free, no extra services, fine at 500K rows. |
| 2026-08-17 | **Parquet on disk as the inter-track interface** | Lets the two tracks hand work off without a shared running service. `CLAUDE.md` §12 |
| 2026-08-17 | **Web UI with node-link graph view** | Product completeness is a judging criterion, and the UI *is* the demo video. |
| 2026-08-17 | Work split: **Lakshay = infra (A0–A8), Shaurya = AI (B0–B7)** | Plays to each person's strengths; they meet only at the §12 contracts. |
| 2026-08-17 | **Strict directory ownership, push straight to `main`, rebase before push** | Two people over three days — PR overhead costs more than it saves once directories don't overlap. `CLAUDE.md` §14 |

---

## Risks being watched

| Risk | Likelihood | If it happens |
|---|---|---|
| ~~HydraDB won't build on macOS/M1~~ | **RETIRED 2026-08-18** | Built and verified. `just db-check` is green. No Neo4j fallback needed. |
| **HydraDB's Cypher subset is narrow** — no list properties, no `IS NULL`/`IN`, integer node ids only | **High** | Discovered 2026-08-18, documented in `CLAUDE.md` §5.1. Forces two data-model decisions before loading: how to store `aliases[]`, and how to express "currently true" without `valid_to IS NULL`. **Decide before A4/B4 build on the wrong assumption.** |
| **HydraDB path procedures too slow at real graph scale** | Medium | Passing on a 3-node toy graph proves nothing. Re-test `algo.SSpaths` after the real load. If slow: cap `maxLen` at 2 and pre-filter candidate endpoints through Layer 1. |
| **8 GB RAM insufficient for the full 500K pipeline** | Medium | Drop to per-source slices (`just fetch-data --slices N`) and state the reduced corpus honestly in the README. Do not silently ship partial coverage. Better: run full-corpus stages on whichever teammate machine has more RAM. |
| **Free-tier LLM quota exhausted at a bad moment** | Medium | All LLM stages are disk-cached and resumable. Keep a second provider configured in `.env`. Remember the bench's own scorer burns quota too. |
| **UI left to the last hours** | Medium | The UI is the demo video. If Aug 20 morning arrives without it, cut features from it rather than skipping it. |
| **Running out of time before eval** | High | Cut order is fixed in `CLAUDE.md` §13. Follow it rather than improvising under pressure. |

---

## How to update this file

Touch only the **Status** block (keep it describing *right now*), plus a **Decisions** row when something is settled that shouldn't be reopened, or a **Risks** row when a new one appears.

Everything else — what you did this session, what broke, how you fixed it, where to pick up — goes in `progress/track-a.md` or `progress/track-b.md`. Record failures and dead ends there too; "tried X, failed because Y" is the most valuable thing in those files.
