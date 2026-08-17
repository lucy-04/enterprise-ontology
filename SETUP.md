# Setup — running this project from scratch

Everything needed to go from a fresh clone to a working system, in order.

> **Status legend.** Steps are marked as we actually verify them on real hardware:
> - ✅ **VERIFIED** — run successfully on this machine, exactly as written
> - 🔶 **PLANNED** — written from the design, not yet executed. May be wrong. Fix it here when you run it.
>
> Current overall status: **project scaffold not yet built.** Everything below is 🔶 except the prerequisite versions, which were surveyed on 2026-08-17. See `PROGRESS.md` for what's actually done.

---

## 0. Why there is no Docker

Deliberate choice, not an oversight. The development machine has **8 GB of RAM**, and Docker Desktop on macOS reserves 2–3 GB for its Linux VM before running anything. HydraDB compiles and runs natively on Apple Silicon, the Python pipeline is a `uv` virtualenv, and the UI is static files. Adding a container layer would cost a quarter of available memory and buy nothing.

If you are running this on a bigger Linux box and want containers, the pieces are all standard — but the memory-conscious defaults in §5 exist because of the 8 GB target and can be relaxed.

---

## 1. Hardware and OS this was built on

| | |
|---|---|
| Machine | Apple M1, 8 cores, **8 GB RAM** |
| OS | macOS (Darwin 25.5.0) |
| Free disk needed | **~40 GB** (corpus zip ~? GB + extracted docs + Parquet + indexes + HydraDB build artifacts) |

**The 8 GB of RAM is the binding constraint on this project.** The corpus is ~500K documents. Nothing in this pipeline may load the full corpus into memory. Every stage streams: Parquet row-group at a time, embeddings in batches, Splink on its DuckDB backend spilling to disk. If you are adapting this to a larger machine you can raise the batch sizes in `config.yaml`, but do not remove the streaming.

---

## 2. Prerequisites

Versions confirmed present on the dev machine 2026-08-17:

| Tool | Required | Found | Install if missing |
|---|---|---|---|
| Rust | 1.91+ (HydraDB) | ✅ 1.97.0 | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` |
| `uv` | any recent | ✅ 0.11.17 | `brew install uv` |
| Python | 3.12 (project) | ⚠️ system is 3.9.6 — too old | handled by `uv`, see §3 |
| Node | 18+ (UI build only) | ✅ 24.16.0 | `brew install node` |
| Homebrew | any | ✅ 6.0.1 | https://brew.sh |
| `just` | any | ❌ missing | `brew install just` |
| HydraDB C deps | — | ❌ not yet installed | `brew install libcypher-parser suite-sparse` (see §4) |

Do **not** rely on the system Python 3.9 — Splink and modern pyarrow need newer. `uv` pins 3.12 inside the project and never touches system Python.

```bash
brew install just
```

---

## 3. Project environment 🔶

```bash
git clone <this-repo> && cd hydraHack
uv sync                    # creates .venv with Python 3.12 and installs all deps
source .venv/bin/activate  # or prefix commands with `uv run`
just --list                # see every available task
```

`uv sync` reads `pyproject.toml` + `uv.lock`. It is reproducible — do not `pip install` into the venv by hand.

---

## 4. HydraDB — build and run locally ✅ instructions verified against the repo

No cloud account needed. HydraDB runs as a local node speaking Bolt (Neo4j-driver compatible) and HTTP.

**4a. System dependencies.**

```bash
xcode-select --install    # if not already present
brew install just cmake pkg-config llvm suite-sparse
brew install cleishm/neo4j/libcypher-parser
```

> ⚠️ **`brew install libcypher-parser` does NOT work** — it is not in homebrew-core and fails with `No available formula`. You must use the fully-qualified `cleishm/neo4j/libcypher-parser`, which adds the tap automatically. We hit this; it silently aborted the whole install line.

No `PKG_CONFIG_PATH` export is needed — `libcypher-parser` is not keg-only, so Homebrew links `cypher-parser.pc` onto the default search path.

**4b. Clone HydraDB** *outside* this repo — it is a dependency, not our source. Default expected location is `../hydradb`; override with `HYDRADB_DIR` in `.env`.

```bash
cd ~/Dev && git clone https://github.com/hydra-db/hydradb.git
```

**4c. Verify its build prerequisites, then its own smoke test:**

```bash
just db-native-check   # confirms cypher-parser + GraphBLAS are discoverable
just db-smoke          # HydraDB's own write/traverse/reopen/verify harness
```

> ⚠️ On an 8 GB machine a Rust build this size can thrash. If cargo gets OOM-killed: `CARGO_BUILD_JOBS=4 just db-smoke`.

**4d. Start the node** — leave it running in its own terminal:

```bash
just db-up
```

It runs in the **foreground and never returns — that is it working, not hanging.** It listens on:

| Endpoint | Address | Purpose |
|---|---|---|
| Bolt | `127.0.0.1:7687` | Neo4j-driver-compatible queries |
| HTTP | `127.0.0.1:8443` | JSON / NDJSON query API |
| Admin | `127.0.0.1:9090` | readiness + Prometheus metrics |

Two non-obvious things `just db-up` handles for you, both of which cost real debugging time if missed:
- **`RUST_MIN_STACK=33554432`** — graph-node's async query futures exceed the default thread stack. Without it the node builds, serves `/readyz`, and then *aborts on the first query*.
- **`BINDGEN_EXTRA_CLANG_ARGS` / `LIBRARY_PATH`** from `brew --prefix` — cargo is invoked directly and does not inherit HydraDB's own justfile exports, so without these the build fails at bindgen with `'cypher-parser.h' file not found`.

**4e. Prove it actually works** (from a second terminal):

```bash
just db-check
```

This round-trips a write, reads it back over both HTTP and Bolt, and then runs `algo.SPpaths` across a 3-node graph. **A listening port is not proof; a round-tripped write is.** The path procedure is the one that matters — Layer 2's whole design rests on native bounded traversal.

**If HydraDB will not build.** Documented dev fallback: run local Neo4j, point `HYDRA_URI` at it, keep building everything else (same Bolt driver, same Cypher — a connection-string change). **Development unblock only.** Submitted results and the demo must run on HydraDB, since meaningful HydraDB usage is a scored requirement and a separate award. If you use the fallback, record it in `PROGRESS.md` and treat restoring HydraDB as top priority.

**If HydraDB will not build.** There is a documented dev fallback: run local Neo4j, point `HYDRA_URI` at it, and keep building the rest of the pipeline (same Bolt driver, same Cypher — a connection-string change). **This is a development unblock only.** The submitted results and the demo must run against HydraDB, because meaningful HydraDB usage is a scored requirement and a separate award. If you use the fallback, log it in `PROGRESS.md` and treat restoring HydraDB as top priority.

---

## 5. Configuration 🔶

```bash
cp .env.example .env
```

Fill in:

| Variable | What it is | Notes |
|---|---|---|
| `HYDRA_URI` | Bolt URI of the local node | e.g. `bolt://localhost:7687` |
| `HYDRA_USER` / `HYDRA_PASSWORD` | Bolt auth | blank for a local node with auth disabled |
| `LLM_PROVIDER` | `gemini` \| `groq` \| `openrouter` \| `ollama` | we are on **free tiers** — see below |
| `LLM_API_KEY` | key for the above | — |
| `LLM_MODEL_CHEAP` | bulk, low-stakes calls | only used by the optional prose extractor |
| `LLM_MODEL_STRONG` | adjudication + answer writing | the one that matters |
| `EVAL_LLM_PROVIDER` / `EVAL_LLM_API_KEY` | used by the **bench's own scorer**, separate from ours | can be the same key |
| `DATA_DIR` | where the corpus lives | default `./data` |
| `EMBED_BATCH` | embedding batch size | default `64` — **raise only if you have >8 GB RAM** |

**Free-tier reality check.** Providers cap requests per minute and per day. The pipeline is built to stay under ~2,000 total LLM calls (see `CLAUDE.md` §7.2) and every LLM stage is **resumable and disk-cached by document/pair ID**, so a rate-limit stop costs you nothing but time — rerun the same command and it picks up where it left off. Never delete `data/cache/llm/` casually.

---

## 6. Get the corpus ✅ verified against release v1.0.0

```bash
just fetch-data
```

Downloads `all_documents.zip` (**1.26 GB**, ~500K `.txt` files), `questions.jsonl` (500) and `extra_questions.jsonl` (100) into `$DATA_DIR/raw/`, unpacks, and prints a per-source document count. Resumable — a partial download continues rather than restarting.

Budget real time for this: on a home connection it ran at roughly 8 MB/min while competing with other downloads, so **~2–3 hours**. Start it before you need it.

Much faster for iteration — one slice per source (each ≤5,000 docs, 1–26 MB):

```bash
just fetch-data --slices 1
```

Optional secondary dataset for the entity-resolution spot check (`CLAUDE.md` §3.2):

```bash
just fetch-herb
```

### What's actually in questions.jsonl

Useful discovery: **each question ships with its gold answer and gold document ids.** Fields are `question_id`, `question_type`, `source_types`, `question`, `expected_doc_ids`, `gold_answer`, `answer_facts`.

That means retrieval recall can be measured directly and for free, without invoking the LLM-judge scorer — use that for fast iteration and save the paid scorer for real checkpoints.

**Measured category counts (v1.0.0) — these differ from `CLAUDE.md` §3.1, which was written from an older quickstart:**

| Category | Actual | §3.1 said |
|---|---|---|
| basic | 175 | 200 |
| semantic | 125 | 100 |
| intra_document_reasoning | 40 | 50 |
| project_related | 40 | 40 |
| constrained | 30 | 30 |
| conflicting_info | 20 | 20 |
| completeness | 20 | 10 |
| miscellaneous | 20 | 20 |
| info_not_found | 20 | 20 |
| high_level | 10 | 10 |

30 questions have no `expected_doc_ids` — the 20 `info_not_found` plus the 10 `high_level`.

**Source references across the 500 questions** — note how badly this diverges from corpus size:

| Source | Questions | Corpus docs |
|---|---|---|
| confluence | 114 | ~5,000 |
| jira | 100 | ~6,000 |
| slack | 79 | ~275,000 |
| github | 60 | ~8,000 |
| google_drive | 60 | ~25,000 |
| linear | 58 | ~35,000 |
| gmail | 55 | ~120,000 |
| hubspot | 34 | ~15,000 |
| fireflies | 25 | ~10,000 |

Confluence and Jira are the two *smallest* sources but carry the most questions; Slack is 275K documents for 79. Graph extraction is therefore prioritised smallest-and-most-structured first (`config.yaml` → `corpus.extract_priority`). Note the source is spelled **`google_drive`**, not `drive`.

---

## 7. Run the pipeline 🔶

Stages are independent and each writes Parquet to disk, so you can stop and resume between any two. Run in order the first time:

```bash
just normalize     # raw .txt  ->  data/normalized/{source}/*.parquet        (no LLM, ~minutes)
just index         # normalized -> SQLite FTS5 + local vector index          (no LLM, ~30-60 min)
just extract       # normalized -> data/candidates/{mentions,relations}.parquet  (rule-based, no LLM)
just resolve       # candidates -> data/resolved/entities.parquet            (Splink + a few LLM calls)
just conflicts     # resolved   -> data/graph/edges.parquet, bi-temporal     (a few LLM calls)
just load          # edges/entities -> HydraDB over Bolt
```

Or all of it:

```bash
just pipeline
```

Each stage is idempotent — rerunning skips work already on disk. `just load` drops and reloads the graph by design, since the graph gets regenerated many times during development.

**Expected timing on the 8 GB M1** (fill in real numbers as we measure them):

| Stage | Expected | Actual |
|---|---|---|
| normalize | < 1 hr | _tbd_ |
| index | 30–60 min | _tbd_ |
| extract | minutes | _tbd_ |
| resolve | 10–30 min | _tbd_ |
| conflicts | minutes | _tbd_ |
| load | _tbd_ | _tbd_ |

---

## 8. Run the app 🔶

```bash
just serve     # FastAPI on http://localhost:8000, UI served at /
```

Requires the HydraDB node from §4c to be running. Open http://localhost:8000 — ask a question, see the answer, the documents behind it, the resolved entity with its aliases, and any conflicting facts side by side.

---

## 9. Run the evaluation 🔶

```bash
just eval                 # full 500 questions
just eval --limit 50      # quick iteration
just eval --resume        # continue an interrupted run
```

Writes `answer_evaluation/answers.jsonl`, invokes the EnterpriseRAG-Bench scorer, and renders `answer_evaluation/results.json` into a markdown table (overall + per category) for the README.

Note the scorer is an LLM judge and uses `EVAL_LLM_*` from `.env`, so a full 500-question scoring run consumes free-tier quota of its own. Budget for it — don't discover at 11 PM on the 20th that quota is exhausted.

---

## 10. Troubleshooting

Problems and their fixes get recorded here as they are hit. See `PROGRESS.md` for the full issue log with context.

| Symptom | Cause | Fix |
|---|---|---|
| _none recorded yet_ | | |
