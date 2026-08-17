# Track A log — infra / backend (Lakshay)

**Only Track A writes to this file.** Append-only. Log dead ends as well as wins — "tried X, failed because Y" is what stops a future session repeating it.

Track A scope: `src/ingest/`, `src/index/`, `src/graph/`, `src/api/`, `src/ui/`, `src/eval/`. Task list in `CLAUDE.md` §11 (A0–A8).

---

## Task status

| Task | Status | Notes |
|---|---|---|
| A0 scaffold + HydraDB spike | 🔨 scaffold done, spike pending deps | contracts committed; HydraDB build blocked on brew |
| A1 corpus acquisition | 🔨 in progress | `fetch.py` written; 1.26 GB download running |
| A2 normalizers (9 sources) | 🔲 not started | |
| A3 Layer 1 index (FTS5 + vectors) | 🔲 not started | |
| A4 HydraDB loader | 🔲 not started | needs B's `entities.parquet` / `edges.parquet` |
| A5 query layer (`GraphClient`) | 🔲 not started | stub committed night one, per §14.3 |
| A6 FastAPI service | 🔲 not started | |
| A7 demo UI | 🔲 not started | **this is the video — don't leave to last** |
| A8 eval runner | 🔲 not started | |

Legend: 🔲 not started · 🔨 in progress · ✅ done · ⚠️ done but shaky · ❌ blocked

---

## Session log

### 2026-08-17, ~22:20–23:15 IST — planning and scaffolding prep

**Goal:** turn the brief into an executable two-person plan; set up tracking; prepare to start A0.

**Done:**
- Architecture agreed with owner (two-layer; see `PROGRESS.md` → Decisions).
- Rewrote `CLAUDE.md`: new §7 architecture, §8 settled decisions, §11 team split, §12 inter-track contracts, §13 schedule, §14 parallel-work and merge-conflict protocol.
- Wrote `SETUP.md` (install → run, steps marked verified vs. planned) and the progress files.
- Surveyed the dev machine — findings below.

**Environment survey (2026-08-17):**

| Finding | Implication |
|---|---|
| **8 GB RAM, Apple M1, 8 cores** | ⚠️ Binding constraint. Nothing loads the full corpus into memory — every stage streams. Embedding batch capped at 64. Splink on its disk-spilling DuckDB backend. Rust builds may need `CARGO_BUILD_JOBS=4`. |
| 93 GB free disk | Enough, not generous. Watch it during the corpus unzip + Rust build. |
| System Python **3.9.6** | Too old for Splink / modern pyarrow. Project pins 3.12 via `uv`. Never use system Python. |
| Rust **1.97.0** | Above HydraDB's 1.91 minimum ✅ |
| `uv` 0.11.17, Node 24.16.0, Homebrew 6.0.1 | Present ✅ |
| `just` not installed | `brew install just` — needed before anything runs |
| Docker not installed | Confirmed the no-Docker decision rather than working around it |
| All 3 existing commits dated 2026-08-17 | ✅ Clears the "no commits before Aug 12" submission rule |

---

### 2026-08-17, ~23:00–23:30 IST — A0 scaffold

**Goal:** scaffold the repo, commit the frozen contracts so Track B can start, begin the HydraDB spike.

**Done:**
- Scaffolded `src/{common,ingest,index,graph,api,ui,eval,extract,resolve,conflicts,agent,llm}`, `tests/`, `ontology/`, `docs/`.
- `pyproject.toml` with the **frozen dependency set for both tracks** (§14.2) — one commit, nobody edits it again.
- `justfile` + `just/infra.just` (A) + `just/ai.just` (B) — split so the two tracks never conflict on task definitions.
- `.gitignore`, `.env.example`, `config.yaml` — every key both tracks will need defined up front.
- **`src/common/schemas.py`** — the frozen §12 contract, with a `test_contracts.py` suite guarding it.
- **`src/graph/client.py`** — `GraphClient` stub, all 7 signatures, `NotBuiltYetError` bodies.
- `src/graph/check.py` — the HydraDB proof script behind `just db-check`.
- `src/ingest/fetch.py` — real, resumable corpus downloader.
- Track A module stubs so every `just` task fails with a clear message, not an import error.
- Downloaded `questions.jsonl` + `extra_questions.jsonl`; full corpus download running.

**Issues hit — all resolved, see table below:** #1 wrong brew formula, #2 uv sync readme, #3 misleading exit code.

**Key findings:**

1. **HydraDB's README has exact local-run instructions** — much better than the guesses in `CLAUDE.md` §5. Two things that would have cost hours:
   - `RUST_MIN_STACK=33554432` is **required**; without it the node serves `/readyz` then aborts on the first query.
   - On macOS, `BINDGEN_EXTRA_CLANG_ARGS` + `LIBRARY_PATH` must be exported from `brew --prefix`, because cargo doesn't inherit HydraDB's justfile exports. Without them the build dies at bindgen with `'cypher-parser.h' file not found`.
   Both are baked into `just db-up`. Ports: Bolt 7687, HTTP 8443, admin 9090. Local auth token is `local-development-token-32-bytes`.

2. **The corpus is only 1.26 GB zipped** — far smaller than budgeted. Disk is not a constraint.

3. **`questions.jsonl` ships gold answers and gold doc ids** (`expected_doc_ids`, `gold_answer`, `answer_facts`). We can measure retrieval recall directly and for free, saving the paid LLM-judge scorer for real checkpoints. Big win for iteration speed.

4. **Question counts differ from `CLAUDE.md` §3.1** (which was written from an older quickstart): basic 175 not 200, semantic 125 not 100, intra_document_reasoning 40 not 50, completeness 20 not 10. Rest match. Recorded in `SETUP.md` §6.

5. **⭐ The question distribution is wildly disproportionate to corpus size.** Confluence (114 questions / ~5K docs) and Jira (100 / ~6K) dominate, while Slack is 79 questions across ~275K docs. The smallest, most structured sources carry most of the score — which is exactly where rule-based extraction is strongest. Drove `config.yaml` → `corpus.extract_priority`, ordered confluence → jira → github → linear → hubspot → google_drive → fireflies → gmail → slack. **If the clock or the 8 GB machine forces a cut, cut from the right end.**

6. The source is spelled **`google_drive`**, not `drive`. Fixed in `SOURCE_TYPES` before the contract was committed; `test_contracts.py` guards it.

**Next:** finish brew install → `just db-native-check` → `just db-smoke` → `just db-up` → `just db-check`. Then A2 normalizers against the corpus once it finishes downloading.

---

## Issues

| # | Issue | Status | Cause / fix |
|---|---|---|---|
| 1 | `brew install libcypher-parser` failed with `No available formula` and **silently aborted the entire install line** — nothing got installed | ✅ resolved | Not in homebrew-core. Must use the tap: `brew install cleishm/neo4j/libcypher-parser`. Also needs `cmake pkg-config llvm`, which weren't in my original list. Documented in `SETUP.md` §4a. |
| 2 | `uv sync` failed: `OSError: Readme file does not exist: README.md` | ✅ resolved | `pyproject.toml` declared `readme = "README.md"`, but §14.2 defers creating the README to Aug 20 to avoid a submission-day conflict. Removed the key with a comment to restore it when the README lands. |
| 3 | My own check `brew list --versions X \| head -1 \|\| echo MISSING` always reported success | ✅ resolved | Pipeline exit status is `head`'s, not `brew`'s. Capture to a variable and test it instead. Worth remembering — it produced a confidently wrong "everything installed" reading. |
