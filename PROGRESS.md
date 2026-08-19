# Progress — where this project actually is

`CLAUDE.md` says what we intend to build. **This file says where we actually are.** When they disagree, this file is right.

**If you are an agent or a person picking this up cold:** read the Status block, then your track's log, then Risks. Don't reopen anything in Decisions — those were settled with the project owner.

> ⚠️ **Keep edits to this file small** — both people touch it. Detailed session notes go in your own track log, never here:
> - **`progress/track-a.md`** — Track A (infra) session log and issues
> - **`progress/track-b.md`** — Track B (AI) session log and issues

---

## Status — 2026-08-19 14:30 PT

| | |
|---|---|
| **Phase** | **End to end works.** Question in → answer + provenance out, through API and UI. All processes currently **stopped** (machine cooling); restart with `just db-up` + `just serve`. |
| **Deadline** | 2026-08-20 23:59 PT — **~33 hours left** |
| **Track A** (Lakshay, infra) | A0–A7 ✅ (A1 full corpus in). Search over **511,958 docs**; graph in HydraDB; API + UI built. Only **A8 eval runner** left. UI **not yet visually verified**. |
| **Track B** (Shaurya, AI) | **B0–B7 complete**; pipeline works. ⚠️ **4 fixes needed — see `fix.md`.** #1 is one line and demo-blocking (answers print raw entity ids). Gemini key is now live and abstention works 8/8. |
| **HydraDB** | ✅ **WORKING, VERIFIED, AND LOADED.** `just db-check` green; `just load` writes the real resolved graph. Write-side Cypher rules measured and written up in `CLAUDE.md` §5.1.1 — read that before writing any Cypher. |
| **Corpus** | ✅ **511,961 documents** downloaded, verified, normalized and keyword-indexed. Per-source counts match the brief. Vector embeddings ~3% done then stopped — low value (cr@20 0.849 keyword-only vs 0.861 hybrid), safe to leave. |
| **Eval score** | Layer 1 offline vs gold doc ids: **cr@20 = 0.861** hybrid / 0.849 keyword-only (measured on the 45K subset). No official LLM-judge run yet — that is A8. |
| **Blocked on** | Two calls for the owner: (a) apply `fix.md` #1, (b) **rebuild the graph over all 512K docs** — Layer 2 still runs on the 180-doc fixture while Layer 1 covers 512K. (b) is the biggest score gain left. |

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
| ~~HydraDB's Cypher subset is narrow~~ | **RETIRED 2026-08-19** | Both data-model questions are settled **in code**: aliases are owner-scoped `:Alias` nodes, and validity is an `is_current` boolean plus a far-future sentinel. Track B had independently built to the same answers, so the §12 contract holds. Write-side rules are in `CLAUDE.md` §5.1.1 and encoded once in `src/graph/bolt.py`. |
| **HydraDB path procedures too slow at real graph scale** | Medium | Passing on a 3-node toy graph proves nothing. Re-test `algo.SSpaths` after the real load. If slow: cap `maxLen` at 2 and pre-filter candidate endpoints through Layer 1. |
| **8 GB RAM insufficient for the full 500K pipeline** | Medium | Drop to per-source slices (`just fetch-data --slices N`) and state the reduced corpus honestly in the README. Do not silently ship partial coverage. Better: run full-corpus stages on whichever teammate machine has more RAM. |
| **Free-tier LLM quota exhausted at a bad moment** | Medium | All LLM stages are disk-cached and resumable. Keep a second provider configured in `.env`. Remember the bench's own scorer burns quota too. |
| ~~UI left to the last hours~~ | **RETIRED 2026-08-20** | Built (`src/ui/`), served by `just serve`, cytoscape vendored locally so no CDN dependency while recording. Still needs a human to look at it. |
| **Layer 2 graph is 180 docs while Layer 1 is 512K** | **High** | The whole entity-resolution / conflict / multi-hop story currently demos over 180 documents. Run `just extract && just resolve && just conflicts && just load` at full scale. Watch RAM — Splink on 8 GB is the risk. |
| **Free-tier model choice silently disables the abstention gate** | Medium | Thinking models return empty at low `max_tokens`, and `grade()` reads empty as "proceed". Pinned to `gemini-3.5-flash` (3/3 reliable). If abstention regresses, check the model before the logic. See `fix.md` #3. |
| **Running out of time before eval** | High | Cut order is fixed in `CLAUDE.md` §13. Follow it rather than improvising under pressure. |

---

## How to update this file

Touch only the **Status** block (keep it describing *right now*), plus a **Decisions** row when something is settled that shouldn't be reopened, or a **Risks** row when a new one appears.

Everything else — what you did this session, what broke, how you fixed it, where to pick up — goes in `progress/track-a.md` or `progress/track-b.md`. Record failures and dead ends there too; "tried X, failed because Y" is the most valuable thing in those files.
