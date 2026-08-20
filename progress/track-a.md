# Track A log — infra / backend (Lakshay)

**Only Track A writes to this file.** Append-only. Log dead ends as well as wins — "tried X, failed because Y" is what stops a future session repeating it.

Track A scope: `src/ingest/`, `src/index/`, `src/graph/`, `src/api/`, `src/ui/`, `src/eval/`. Task list in `CLAUDE.md` §11 (A0–A8).

---

## Task status

| Task | Status | Notes |
|---|---|---|
| A0 scaffold + HydraDB spike | ✅ done | `just db-check` green — HTTP + Bolt + `algo.SPpaths` + var-length all verified |
| A1 corpus acquisition | ✅ done | full 1.26 GB downloaded + verified; **511,961 unique docs**, per-source counts match the brief |
| A2 normalizers (9 sources) | ✅ done | all 9 parse; 22 tests green; `normalized_sample.parquet` fixture generated for Track B |
| A3 Layer 1 index (FTS5 + vectors) | ✅ done | 45K docs indexed; hybrid **conditional recall@20 = 0.861**. `just recall` measures it free/offline |
| A4 HydraDB loader | ✅ done | 1,046 nodes + 1,072 edges into HydraDB in 2.2s from the fixture. Drop-and-reload, idempotent |
| A5 query layer (`GraphClient`) | ✅ done | Layer 2 complete: `find_entity` (alias-aware), `neighbors`, `paths` (all 3 native procedures), `facts_about`, `get_entity`, `cypher`. Layer 1 (`search`/`get_docs`) still A3 |
| A6 FastAPI service | ✅ done | 10 endpoints, every answer ships its trace; 19 tests |
| A7 demo UI | ⚠️ built, unverified | single page: answer + grade + conflicts side-by-side + alias merge + cytoscape graph. Vendored, no CDN. **Nobody has looked at it rendered yet** |
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
| 4 | **§7.4 claimed slack is `handle (team): text`. Measured: only ~2/20 docs use that; ~18/20 use a bare `speaker:`.** First normalizer run found 0.4 speakers/doc and left 18/20 docs with no author at all | ✅ resolved | The claim came from reading a single document. Regex now accepts both forms plus `@mentions`; result went 0.4 → 6.0 speakers/doc, zero empty docs. `test_slack_finds_speakers_in_the_bare_form` guards it. **Lesson: sample more than one file before writing a spec.** |
| 5 | Prose labels became people — `Requirements:`, `Impact:`, `Auto-summary (auto-generated, may be partial):` all matched the speaker regex | ✅ resolved | Added a `NOT_A_SPEAKER` stopword set plus a recurrence test (a real speaker either talks twice or looks like a handle). `test_prose_labels_are_not_treated_as_people` guards it. |
| 6 | Two concurrent `brew install` processes deadlocked on the same download lock; gcc also failed once with `curl (92) HTTP/2 PROTOCOL_ERROR` | ✅ resolved | Never run two brew installs in parallel. Killed the stale process, retried, succeeded. The HTTP/2 error was transient bandwidth contention. |
| 8 | `just db-check` failed with HTTP 400 on every query | ✅ resolved | My check script used plain Cypher. HydraDB's subset rejects bare-node `CREATE`, multi-hop `CREATE` chains, string node ids and list properties. Rewrote the script against the real subset; now green. Full constraint list in `CLAUDE.md` §5.1. |
| 7 | **`just smoke` failed (exit 101)** — cargo could not fetch crates: `transfer too slow: failed to transfer more than 10 bytes in 30s`, repeated, then `failed to download aws-lc-rs`. `brew install llvm` failed the same way | ✅ resolved | **Self-inflicted.** I had the 1.26 GB corpus download, two brew installs, `uv sync` (2.7 GB of torch) and a cargo fetch all running at once; cargo's default patience is 10 bytes/30s and it gave up. Verified afterwards that the network is fine (200 OK, 192 KB/s to crates.io). Retrying serially with `CARGO_NET_RETRY=10 CARGO_HTTP_TIMEOUT=180 CARGO_HTTP_LOW_SPEED_LIMIT=1`. **Lesson: run one network-heavy job at a time on this connection.** |

---

### 2026-08-18, ~00:00–00:40 IST — environment verified, all 9 formats mapped

**Done:**
- `uv sync` complete. **All dependencies import and work**, verified individually: pyarrow 25.0.1, duckdb 1.5.5, pandas 3.0.5, numpy 2.5.2, httpx, neo4j 6.2.0, fastapi, splink 4.0.16, rapidfuzz, openai 3.1.0, tenacity, torch 2.13.0 (**MPS available**), sentence-transformers 5.7.0.
- `uv run pytest` — 6/6 contract tests pass.
- `just --list` confirms the split justfile (`just/infra.just` + `just/ai.just`) imports correctly, so the two tracks never share a task file.
- Verified the real document format for **all nine sources** and wrote it up as `CLAUDE.md` §7.4. This corrected a false assumption the whole extraction plan rested on.
- `tests/fixtures/sample_docs/` — 180 real documents, 20 per source, all nine sources. Track B can build and test every extractor with no downloads.

**Findings:**
- **Highest-value discovery: cross-source join keys.** HubSpot cites Fireflies meeting ids (`ff_<date>_<hash>`); Jira cites `SUP-`/`TRACK-`/`OPS-`/`DOC-` tickets and `PR #\d+`. These are deterministic, zero-LLM edges *between different sources* — exactly what multi-hop questions need and what plain search cannot follow.
- **One `X (Y)` pattern family covers four sources** — slack `handle (team):`, jira `Role (Name):`, fireflies `Org (Person) to …`, hubspot `Name (Role)`. One parameterised parser, not four.
- Rule-based extraction is **strong** for gmail/slack/fireflies/hubspot, **partial** for jira/github/linear, **weak** for confluence/google_drive. Since confluence is the #1 question source but the worst graph source, this validates the two-layer split rather than undermining it.
- torch pulled 2.7 GB into the uv cache. Works, but it's the heaviest thing on an 8 GB machine — if embedding turns out to thrash, a static-embedding model (model2vec) would remove torch entirely.

**Decisions taken:**
- Paused the 1.26 GB `all_documents.zip` download at 209 MB; it was starving the HydraDB dependency installs of bandwidth and is not needed tonight. **Resumable** — `just fetch-data` continues from the partial file via a Range request. Restart it before A2.
- Pulled one slice per source instead (~5,000 docs each) — enough to build and test normalizers immediately.

**Still open:** `suite-sparse` (SuiteSparse:GraphBLAS) is still installing — it's building `gcc` as a dependency, which is slow on this machine. **The HydraDB spike is therefore not yet done**, and it remains the night-one gate.

**Next:** wait out `brew install suite-sparse` → `just db-native-check` → `just db-smoke` → `just db-up` → `just db-check`. Then A2 normalizers (fixtures and format spec are ready).

---

### 2026-08-18, ~01:00–01:40 IST — A2 normalizers done

**Done:**
- `src/ingest/normalize.py` — all nine sources parse into the `NormalizedDoc` contract. Streams via `ProcessPoolExecutor`, writes Parquet row groups, never holds the corpus in memory.
- `tests/test_normalize.py` — 16 regression tests against real fixtures. Full suite: **22 passed**.
- `tests/fixtures/normalized_sample.parquet` — 180 docs, 441 KB. **This is Track B's development input** (§14.5).
- HydraDB deps all installed; `just native-check` passes; `just smoke` is compiling.

**Extraction quality on the 180-doc fixture** (avg refs/doc):

| source | authors | mentions | docs w/ cross-refs |
|---|---|---|---|
| gmail | 8.0 | 10.0 | 1/20 |
| slack | 6.0 | 6.4 | 4/20 |
| jira | 0.8 | 4.1 | 15/20 |
| hubspot | 0.4 | 1.9 | 6/20 |
| fireflies | 0.5 | 1.6 | 0/20 |
| confluence | 0.0 | 1.1 | 1/20 |
| google_drive | 0.0 | 0.4 | 4/20 |
| linear | 0.0 | 0.3 | 19/20 |
| github | 0.0 | 0.1 | 15/20 |

Matches §7.4 exactly: gmail/slack carry the people, github/linear/jira carry the cross-source ticket refs, confluence/google_drive carry neither and are Layer 1 sources.

**Issues hit — both fixed, see table:** #4 slack format claim was wrong, #5 prose labels extracted as people.

**Next:** `just smoke` → `just db-up` → `just db-check`. Then A3 (FTS5 + vector index).

---

### 2026-08-18, ~04:30–05:10 IST — HydraDB VERIFIED. Night-one gate cleared.

`just db-check` is green:
```
PASS HTTP write + read round-tripped
PASS Bolt connected and read back (token as password)
PASS algo.SPpaths returned a 2-hop path alice -> bob -> carol
PASS variable-length traversal *1..3 returned 2 nodes
```

`just smoke` passed too (`graph object-store smoke passed at epoch 10`) once run
serially — the earlier failure was my own bandwidth contention, not a real fault.

**⚠️ The big finding: HydraDB's OpenCypher subset is far narrower than `CLAUDE.md` §5 assumed.** Verified empirically and written up as **§5.1**. The three that change our design:

1. **Node ids must be non-negative integers.** String ids are rejected outright, so `canonical_id` cannot be the graph id — the loader needs a stable integer surrogate with `canonical_id` kept as a property.
2. **No list properties.** `aliases[]`, `handles[]`, `emails[]`, `source_doc_ids[]` from the §12 contract cannot be stored as-is. Either join to a delimited string or model aliases as their own nodes — the latter is more graph-native and demos far better.
3. **`IS NULL` is not supported in `WHERE`** (nor `IN`, `CONTAINS`, `ENDS WITH`). **This breaks the bi-temporal model as written** — §11 B4 uses `valid_to = null` for "currently true", which is unqueryable. Needs an explicit `is_current` boolean or a far-future sentinel.

Also confirmed working: labels, edge properties set inline, variable-length paths with a required max, `MERGE`/`SET`/`DELETE` after `MATCH`, `UNWIND` batches. A returned `path` carries full node and relationship properties, so one call gives the whole provenance chain.

Bolt auth is the dev token as password: `("neo4j", "local-development-token-32-bytes")`.

**Next:** decide the two data-model questions above **with Track B** before A4/B4 start, then A3 (FTS5 + vector index).

---

### 2026-08-19, ~12:30 PT — A4 + A5 done. The graph is live in HydraDB.

**Result:** Track B's resolved graph now loads into HydraDB and is queryable through `GraphClient`. `just load` → 1,046 nodes + 1,072 edges in 2.2s. 26 new tests, full suite 79 passed / 1 skipped, ruff clean.

**Files:** `src/graph/bolt.py` (new — connection, batching, property encoding), `src/graph/load.py` (A4), `src/graph/client.py` (A5), `tests/test_graph.py` (new).

#### The expensive part: HydraDB's *write* subset is much narrower than its read subset

§5.1 covered reading. Writing turned out to be a second, mostly undocumented set of constraints. Full table now in **§5.1.1**. Four that cost real time:

1. **Batched writes only work over Bolt.** I spiked the whole batch API against the HTTP `/query` endpoint and every single form failed. The HTTP endpoint routes to the in-process shard API, which takes scalar parameters only. `cypher-compat.md` states this in one sentence near the end of the UNWIND section; the error messages talk about row execution rather than transport, so they actively point away from the real cause. **Read `cypher-compat.md` in the hydradb repo before spiking anything — it is the real spec.**
2. **`e.id` is not projectable.** Selecting a relationship's `id` fails with **"unbound variable e"**. I lost the most time here: the message implies a scoping problem, so I ran a 16-case matrix varying labels, WHERE clauses and projections trying to find the binding rule — and every case passed. The only difference in the failing set was `e.id` in the RETURN. `id` is the relationship's reserved identity. Store a separate `edge_id` and project that.
3. **No bare node creation at all** — `CREATE (n:X {...})` and `MERGE (n:X {...})` both reject. Nodes exist only via the `UNWIND ... MERGE (n {id: row.id}) SET n:Label, ...` upsert, which takes **exactly one** SET label.
4. **Relationship batches need `(src_label, rel_type, dst_label)` grouping**, both endpoints labelled, and every edge property read from the row map — a literal `{is_current: true}` is rejected.

Batch cap is 1024 items (using 500). Measured ~8,800 nodes/s, ~1,600 edges/s.

#### Data-model decisions — the two open questions are now settled, in code

Both were flagged as blockers after the §5.1 discovery. Resolved as recommended, and Track B had independently designed to the same answers in `ontology.yaml`, so the contract holds:

- **Aliases are their own `:Alias` nodes** linked by `HAS_ALIAS`, not a joined string. Two extra properties of the decision worth knowing: alias nodes are **scoped to their owner** (a shared "ben" node would let Ben Carter and Ben Turner be joined by a 2-hop path, inventing a relationship that does not exist), and `HAS_ALIAS` is **excluded from default traversals** because it is structural bookkeeping, not a fact about the world.
- **`is_current` boolean + far-future sentinel** (`4102444800`) for `valid_to`. `GraphClient` maps the sentinel back to `None` so `Edge.is_current` stays truthful and Track B's checks keep working unchanged.
- **Node ids** are a 62-bit blake2b hash of `canonical_id` — stable across reloads with no mapping table to persist. The loader **aborts** on any collision rather than silently merging two entities, which would be precisely the failure this project exists to prevent.

#### Verified working end to end

- **Alias-aware resolution:** `find_entity("karthik_iyer@redwood.com")` → the `Karthik Iyer` Person node with all surface forms. This is the demo's opening shot.
- **Conflict with provenance:** `facts_about(alex, "MEMBER_OF")` returns *both* sides, current first — `Eng-Oncall (current)` and `Support (was true until 2026-03-15, now superseded)`, each with its source doc id.
- **All three native path procedures** through `paths()`: `SPpaths` (1→1), `MSpaths` (many→many, grouped by label pair), `SSpaths` (open-ended). Paths carry full node/edge properties, so `Path.doc_ids` gives the citation list in one call.

#### Two things for Track B

1. **`GraphClient.get_entity(cid)` now exists.** This closes the gap noted in `progress/track-b.md` (B5/B6): `format_edge_fact` currently prints the destination as a raw `ent_...` id when it is not in the resolved set. With `get_entity` the `naming()` closure in `router._gather` can resolve it to a real name — a one-line change in Track B's file, which I have deliberately not made.
2. **The router abstains on everything right now, and that is correct.** `search()` still raises `NotBuiltYetError` until A3 lands, so there are no document hits and the grade gate correctly refuses to answer. Graph facts *do* flow (verified above). Expect this to resolve when A3 lands, not before.

**Next:** A3 (FTS5 + vector index) — it is now the only thing standing between a loaded graph and end-to-end answers.

---

### 2026-08-20, ~01:00–02:00 IST — A3 done. Layer 1 is live and measured.

**Result:** 45,000 documents indexed (SQLite FTS5 + a float16 vector memmap), wired into `GraphClient.search()` / `.get_docs()`. Hybrid **conditional recall@20 = 0.861**. 21 new index tests + 1 client test; my suites are 48 green, ruff clean.

**Files:** `src/index/build.py` (builder), `src/index/search.py` (query side), `src/index/recall.py` (measurement), `tests/test_index.py`, plus Layer 1 wired into `src/graph/client.py`. `just recall` added.

Also ran the two prerequisite stages that had never been run at scale: unzipped the nine slices (45K docs) and normalized them. A2 held up — 45K docs parsed in ~15s with no errors.

#### The measurement that changed how to read every other number

First keyword-only run looked bad: recall@20 = 0.443. It is not.

**Only 52.1% of questions have a gold document in this index**, because we hold 45K of ~500K documents. Raw recall is hard-capped by that. So `src/index/recall.py` now reports both:

- `r@k` — recall over all questions, bounded by coverage
- `cr@k` — recall over questions whose gold document is *actually indexed*: the retriever's own score

**Judge the retriever by `cr@k` until the full corpus is in.** Anyone reading `r@k` cold will conclude retrieval is broken when the real gap is the download.

```
                    keyword   vector   hybrid     (cr@20)
basic                 0.890    0.740    0.945
semantic              0.604    0.302    0.566
constrained           0.960    0.920    1.000
conflicting_info      0.947    0.789    0.947
intra_doc_reasoning   1.000    1.000    1.000
project_related       0.974    0.795    0.974
OVERALL               0.849    0.686    0.861
```

#### Two hypotheses tested and one rejected — worth not re-testing

Vector search underperforms keyword badly here (0.686 vs 0.849), which is backwards from expectation. I chased two causes:

1. **BGE query instruction prefix — REJECTED.** `bge-*-en-v1.5` is documented as needing `"Represent this sentence for searching relevant passages: "` on queries. Measured on the 53 reachable semantic questions: 0.302 without it, **0.283 with it**. It does not help on this corpus. Don't re-add it.
2. **Truncation — real but partial.** 97% of documents are longer than the 2000 chars we embed (median 5,720). But of semantic gold content, 26 instances fall inside the first 2000 chars and only 7 beyond, so truncation explains ~21% of misses, not the bulk.

**Raising `max_chars_per_doc` would do nothing.** `bge-small-en-v1.5` caps at 512 tokens ≈ 2000 chars, so the tokenizer discards the rest regardless. The only real fix is **chunking** (embed passages, not documents) — which at 500K docs × ~4 chunks is roughly 10+ hours of embedding on this machine. Not affordable before the deadline. Whole-document embedding is the deliberate, constrained choice; keyword carries semantic retrieval for now.

#### Fusion weighting — swept, not guessed

Equal-weight RRF is best. Swept keyword:vector from 1:0 to 0:1 over the 245 reachable questions — `1:1` gave 0.861, keyword-only 0.849, and anything that down-weights keyword falls off a cliff (0.75:1 → 0.743). Left at equal weights.

**A measurement bug found and fixed:** the harness originally issued one query at `max(ks)` and sliced it for each k. Hybrid sizes its candidate pool from k, so slicing a larger run measured a configuration the system never uses — it under-reported cr@20 as 0.849. Each k is now its own query.

#### ⚠️ Handoff to Track B — abstention is currently broken, and it is 20 questions of free points

`tests/test_agent.py::test_router_never_raises_against_unimplemented_client` now **fails**, and it is pointing at something real rather than just being stale.

The test assumed "base `GraphClient` raises from every method", which was only true while A3 was a stub. But the deeper issue: with Layer 1 live, `search()` always returns *something*, and `grade()` in `src/agent/synthesize.py:61-63` returns `True` whenever context is non-empty if no LLM is configured. So the gate never fires.

Measured: **abstains on 0/8 `info_not_found` questions, and does not abstain on pure gibberish** (`"zzqqxx vlorptang mimsy borogove"` → confident answer, 1 citation).

The LLM grader immediately below that branch is strict and would very likely fix it — **there is no `.env` and no `LLM_API_KEY` set**. Highest-value next action for Track B: set the Gemini key and re-measure. Both files are Track B's, so I have not touched them.

**Next:** full corpus (download at ~67%), then re-run normalize → index → recall at 500K and hand Track B a full-scale mentions/entities run. Then A6/A7/A8.

---

### 2026-08-20, ~02:00–03:00 IST — full corpus rebuild, A6 API, A7 UI, and the Gemini key

**Corpus:** downloaded, verified, and normalized in full — **511,961 unique documents**, per-source counts matching the brief exactly. Keyword index rebuilt over all of them (511,958 rows). Vector embeddings running (slow — see below).

**Two real bugs found in my own earlier work while doing it:**

1. **`find_sources` was non-recursive.** The per-source `*_slice_*.zip` files unpack flat (`jira/*.txt`), but `all_documents.zip` unpacks **nested** (`confluence/applied-ml/eval-harness/*.txt`). A `glob("*.txt")` silently found only the 45K flat slice files and missed the entire 512K corpus — it looks like it worked. Now `rglob`, and deduplicated by `doc_id` rather than by deleting files, so unpacking both layouts in any order is still correct.
2. **A stale `index_meta.json` silently corrupts search.** The manifest records the row count mapping vector offsets back to documents. Rebuilding the keyword index without rebuilding vectors leaves a manifest describing the *old* corpus, and every vector hit then resolves to the wrong document. `build_vector_index` now deletes the manifest **before** embedding, so search degrades to keyword-only mid-rebuild — a correct answer instead of a confidently wrong one.

**Gemini key (thanks Lakshay) — and two model problems behind it:**

- `gemini-2.0-flash` (our configured default) is **retired**; the API 404s and names the replacement.
- The obvious fix, `gemini-flash-latest`, is **worse than useless here**: it is a thinking model, and at the small `max_tokens` this codebase uses its reasoning consumes the entire output budget. Measured **0/3 non-empty** at `max_output_tokens=120`. `grade()` treats an empty response as "grader unavailable, proceed" — so it would have silently disabled the abstention gate on every question while looking fine.
- Settled on **`LLM_MODEL_STRONG=gemini-3.5-flash`** and `LLM_MODEL_CHEAP=gemini-flash-lite-latest`, both measured 3/3 reliable. Cleared `data/cache/llm/` since it held responses from the broken model.

**Abstention now works: 8/8 on `info_not_found`**, and gibberish is refused. It was 0/8 before the key. Real questions still answer correctly.

**A6 API** — `src/api/main.py`. `/ask` (delegates to Track B's router, the only A→B call), plus `/api/resolve`, `/api/facts`, `/subgraph`, `/doc`, `/api/docs`, `/api/search`, `/api/stats`. Every answer ships its full trace, because the demo has to *show* the reasoning, not just the sentence. 19 tests.

**A7 UI** — `src/ui/{index.html,app.js,style.css}`. One page, built around the three demo beats: an answer with its route and grade decision; contradictions rendered side by side (superseded struck through with its end date, current beside it, both with source badges and clickable provenance); resolved entities with every surface form as a chip; and a cytoscape graph where **superseded edges are drawn as red dashed lines**, so the contradiction is visible in the picture too. Cytoscape is **vendored locally** (MIT, 373KB) rather than pulled from a CDN — the demo must not depend on a network while recording.

Added a UI-side fallback: the router extracts entities from capitalised words only, so a lower-case question ("which team is alex on now?") resolves nothing and every graph panel would stay hidden. The page retries via `/api/resolve` for *display only*; the answer is untouched.

**⚠️ Verified by syntax check and by exercising all 8 endpoints it calls — but NOT yet seen rendered in a browser.** The Chrome extension is not connected here. Someone must open `http://localhost:8000` and look at it before the video.

#### ⚠️ Two things blocked on decisions, both outside my lane

1. **Answer text contains raw entity ids.** `_gather` in `src/agent/router.py` builds `name_of` only from entities the question resolved, so a destination falls back to its id. The LLM is handed *"alex MEMBER_OF ent_5eb38a08a7908de8 (current)"* and dutifully echoes the id — the marquee conflict question currently answers with a fragment of a hash. `GraphClient.get_entity(cid)` now exists specifically to fix this; it is a one-line change to that closure. Track B's file, so not touched. **The UI's conflict panel is unaffected** — it resolves names server-side and reads correctly.

2. **The graph is still built from the 180-document fixture.** `extract → resolve → conflicts` have never run on the real corpus, so Layer 2 (entity resolution, conflicts, multi-hop) is demoing over 180 docs while Layer 1 covers 512K. This is a far bigger gap than the vector index, and it is the next thing worth machine time.

**On the embeddings:** ~3% in 15 minutes → roughly 8 hours for the full corpus, and it competes for CPU with everything else. Measured gain from vectors is small (cr@20 0.849 keyword-only → 0.861 hybrid). If the graph pipeline needs the machine, kill the embedding first — keyword-only is a perfectly defensible Layer 1.

**Next:** decide the two items above, then A8 eval runner.

---

### 2026-08-20, ~03:00 IST — everything stopped (machine overheating)

All processes killed at the owner's request: uvicorn, HydraDB `graph-node`, and
the embedding job. Ports 8000 / 7687 / 8443 / 9090 all free. **Nothing was lost**
— the corpus, the keyword index, the graph and all code are on disk.

The embedding run was ~3% through and is the only thing interrupted. It stops
safely: `build_vector_index` deletes `index_meta.json` before it starts, so a
half-finished matrix makes search fall back to keyword-only rather than
resolving hits to the wrong documents. To resume, re-run `just index` (it
rebuilds from scratch — there is no partial-resume, by design, because a
partially-valid vector index is worse than none).

**To restart everything:** `just db-up` in one terminal, `just serve` in another.
No rebuilding needed.

**Wrote `fix.md`** — four issues for Track B, in impact order, with exact line
numbers, reproductions and suggested diffs. #1 (raw entity ids in answers) is a
one-line change and is demo-blocking; #3 (stale default model) is the subtle one
that silently disables the abstention gate.

#### Where the real remaining gap is

Not the vector index — measured gain is 0.849 → 0.861 cr@20 for ~8 hours of
compute. **The gap is that Layer 2 still runs on the 180-document fixture.**
Entity resolution, conflict detection and multi-hop are all demoing over 180
docs while Layer 1 covers 511,958. Running `just extract && just resolve &&
just conflicts && just load` at full scale is the single biggest score and demo
improvement left, and it needs the machine free. Splink on 8 GB is the risk to
watch there.

**Next:** owner decisions on `fix.md` #1 and the full-scale graph rebuild, then
A8 (eval runner) and the README/video.

### 2026-08-20 (afternoon) — A8 eval harness, graph rebuild at 2K, README

**A8 built.** `src/eval/run.py` (runner) + `src/eval/score.py` (offline scorer) + 21 tests.
Recipes: `just eval`, `just score`, `just results`.

Two design decisions worth keeping:

1. **Scoring is split into a free tier and an LLM tier.** `questions.jsonl` ships
   `expected_doc_ids` for 470/500, so Document Recall and Invalid Extra Documents are
   exact set arithmetic. The official scorer is an LLM judge and free tiers die — if
   grading were the only route to a number, an exhausted quota would mean no results at
   all. Deterministic first, judge on top.
2. **A quota guard in the runner.** Track B's router catches its own errors and abstains
   rather than crashing — right for a demo, dangerous for a batch run, because a dead
   quota then looks exactly like a cautious system and 400 fake abstentions land in the
   results table as real decisions. The guard trips on a streak of abstentions with
   **zero LLM calls behind them**; a genuine abstention burned a call to reach its verdict,
   so a cautious run never trips it. Stops cleanly, keeps what it earned.

**Smoke run (15 questions) found three things.**

- *Quota died at question 10.* Gemini free tier exhausted; resets after the deadline.
  `LLM_FALLBACK_*` keys exist in `.env` but are empty and unimplemented. Blocking.
- *Bug in my own scorer.* `to_answer_jsonl()` writes only `{question_id, answer,
  document_ids}` — the benchmark format has nowhere to record that a refusal was
  deliberate, so every polite abstention scored as a confident wrong answer and
  false-confidence read 100% when the truth was 0%. Now reads `traces.jsonl`, with a
  text fallback for when no trace exists. Three tests.
- *The real finding: we over-abstain, we do not hallucinate.* Corrected numbers: false
  confidence 0%, abstention accuracy 100%, **false abstention 77%**.

**Root cause of the over-abstention, and the fix.** Gold document was in the retrieved
set for 10 of 13 questions and the system abstained on 7 of them. Retrieval was never the
problem — the grader was being shown a **320-character keyword window** per document, six
documents max. It was correctly concluding the evidence didn't support an answer.

Measured against the benchmark's own `answer_facts` across all 470 gold documents (free,
offline) — mean fraction of the facts needed to answer that are actually visible:

| evidence/doc | 320 | 2,000 | 4,000 | **8,000** | full |
|---|---|---|---|---|---|
| coverage | 0.241 | 0.386 | 0.495 | **0.618** | 0.669 |

Rewrote `_snippet`: documents that fit pass through whole; longer ones get several windows
around *different* query terms (a doc matching three terms in three paragraphs used to be
shown only around the first), joined with an ellipsis so the model can't read across the
gap. Cap is `index.snippet_chars`, set to 8,000 — 92% of the ceiling for 70% of the
context cost. **Coverage 0.24 → 0.62.** Six new tests.

**Graph rebuilt at 2,000 documents.**
- New `src/ingest/sample.py` (`just sample`) — proportional to the real source mix and
  deterministic by seed, so the sample is reproducible and two scales are comparable.
  Largest-remainder allocation with a one-document floor per source, so a small sample
  never silently drops a whole source. 4 tests.
- 19,396 mentions → 6,810 entities → 10,302 edges. **550 superseded edges, up from 1** —
  the conflict demo now has real material. 0 contested (Shaurya got 5 on his sample).
- Splink peaked comfortably: **min free RAM 2.40 GB** throughout.

**`wipe()` rewritten — DETACH DELETE is the slow direction in HydraDB.**
`MATCH (n:Label) DETACH DELETE n` blew the 30s query timeout part-way and left the graph
half-wiped (aliases went 408 → 67). Measured the actual rate: **~12 nodes/s**, so clearing
14K nodes through Cypher is ~19 minutes. Also learned that `UNWIND` batch node patterns
**reject labels** ("UNWIND batch node patterns do not support labels") — batched deletes
have to match on the id read back from a labelled query. So: `wipe()` now batches, but
refuses past 2,000 nodes and points at the new **`just db-reset`**, which clears the
20 MB store on disk in about a second. Reload after that: 13,848 nodes + 17,340 edges in
**17.4s**.

**Found a demo-blocking bug in Track B's entity resolution — `fix.md` #5.**
The new 2K graph has **0 of 3,207 people carrying both a name and an email**; the most
aliased person has 5 aliases, all case variants. Root cause: `build_person_frame` fills
`surname` for *every* mention, and for an email that value is the whole address
(`karthik_iyer@redwood.com`). The surname guard then sees `{'iyer'}` vs
`{'karthik_iyer@redwood.com'}` and rejects the union — so the name↔email/handle bridge,
which is the "Sam / @soham / S. Ratnaparkhi" mechanism the brief is built around, fires
**zero times**. Proved it by swapping only `surname_of`: **0 → 1,076 bridge unions**, and
through the full pipeline 166 merges with **multi-surname clusters still 0**. Written up
with the diff and a verification command. Not edited — Track B's file (§14.1).

**README written** (§14.2 says Aug 20, and it is). Leads on the two-layer rationale, a
detailed "How HydraDB is used" section aimed at the Best-Use award (native path
procedures, and the three data-model decisions its Cypher subset forced), the ER story
with the before/after table, the bi-temporal conflict model, and an honest limitations
section. Results table is a marked placeholder.

**State:** 153 tests pass, 1 skipped. My files lint clean; 10 ruff nits remain in Track B's files,
left alone. Blocked on a second LLM key and on `fix.md` #5.

### 2026-08-20 (evening) — quota root-caused, full eval running, real recall numbers

**The quota wall was a model choice, not a spent budget.** The 429 said only
"exceeded your current quota", but the full error carries the detail:

```
quotaId:    GenerateRequestsPerDayPerProjectPerModel-FreeTier
quotaValue: 20
model:      gemini-3.5-flash
```

**Twenty requests per day.** `gemini-3.5-flash` — the model `fix.md` #3 recommended
pinning to — is unusable for anything batch. Probed the alternatives: both
`gemini-flash-lite-latest` and `gemini-2.5-flash-lite` answer 3/3 and have a real daily
budget. Switched `LLM_MODEL_STRONG` to `gemini-flash-lite-latest` in `.env`, and wrote
the measured number into `.env.example` so nobody re-picks 3.5-flash.

Lesson for the tracker: **read the whole 429 body.** The `quotaValue` field turns
"we're out of budget, wait until tomorrow" into "wrong model, change one line."

**Smoke set re-run after the snippet fix — the fix works.**

| metric | before | after |
|---|---|---|
| document recall | 15.4% | **46.2%** |
| citation precision | 5.1% | 20.5% |
| false abstention | 76.9% | **53.8%** |
| false confidence | 0% | 0% |
| abstention accuracy | 100% | 100% |

Categories that scored a flat 0% now score 100% on intra-document reasoning, conflicting
info and completeness. Full 500-question run started in the background: healthy at
42/500, **zero quota-starved abstentions**, 2.05 LLM calls/question.

**Full-index recall measured** (`just recall`, 470 gold questions, keyword-only):

| | r@6 | r@12 | r@20 |
|---|---|---|---|
| overall | 0.713 | 0.736 | **0.766** |
| semantic (125 q) | 0.392 | 0.424 | **0.480** |
| basic (175 q) | 0.731 | 0.754 | 0.783 |

Ceiling is now **1.000** — every gold document is indexed, so r@k and cr@k coincide and
the conditional-recall caveat retires. Note the earlier 0.861 was on the 45K subset;
0.766 over the full 512K is the honest number, lower because there are far more
distractors.

Two decisions fall out:
- **Track B's `_MAX_CONTEXT_DOCS = 6` is not a bottleneck** — 0.713 at six documents vs
  0.766 at twenty, about five points. Not worth asking Shaurya to change. Widening what
  the model sees *of each document* was the right lever, and it was in our lane.
- **`semantic` at 0.480 is the weakest category by a wide margin** and it is 125
  questions. That is exactly what vector search fixes, and vectors are the one component
  not switched on. Not attempting it: ~8h of embedding on a machine already running the
  eval, finishing around 03:00 and forcing a re-run afterwards. Documented in the README
  as the clearest remaining win rather than half-done.

**Fixed `/api/stats` reporting `edges: 0`.** It never queried edges at all — the key was
initialised to zero and returned. It also counted only `:Person` while calling the number
"entities". HydraDB rejects an untyped edge pattern (`MATCH ()-[e]->()`), so the totals
are a sum over every relationship type and every label, cached for 120s. Header now reads
511,958 documents / 6,810 entities / 7,038 aliases / 17,340 edges — matching the loader
exactly. Regression test added; a header reading "0 edges" over a working graph reads as
a broken demo on camera.

**UI still not visually verified** — the Chrome extension will not connect. Verified the
data paths instead: `/api/resolve`, `/entity`, `/api/facts`, `/subgraph`, `/doc` all
return correct shapes against the new graph, and the conflict panel resolves names on
both ends of every edge (confirming `fix.md` #1 works end to end).

**Demo conflict candidates** found in the new graph — people with real surnames and clean
employer histories, better material than the bare-first-name clusters:
- `Samir Patel` (`ent_84c9bfd105930b64`) — now Bluecord; was Kiteworks / Oxbridge /
  Healthmetrics / Finetext, superseded 2027-05-06, all from gmail with doc ids.
- `Alex Chen` (`ent_cc287ffcab5e0a27`) — now Vertexlabs; was Enerflow / Novara-Sys /
  Vectorhealth / Cerebrahealth, superseded 2027-11-16.

Avoid `Sam` / `Priya` / `Maria` on camera: they are bare-first-name clusters (238, 30, 20
mentions) and their "conflicts" are an artifact of that merge, not real disagreements.

### 2026-08-20 (night) — pre-recording fixes; UI verified visually for the first time

Owner asked for anything broken to be fixed before recording, and cleared me to edit
across track boundaries. Two of the changes below are in **Track B's files**
(`src/resolve/splink_er.py`, `src/extract/sources.py`) — flagged here and to Shaurya
because §14.1 normally forbids it and he may be editing the same files.

**Applied `fix.md` #5 — the surname guard was killing every name↔email/handle merge.**
`build_person_frame` fills `surname` for every mention, and for an address that value is
the whole surface form, so the guard saw `{'iyer'}` vs `{'karthik_iyer@redwood.com'}` and
rejected. Added `_real_surname()`: address-shaped mentions (an `email_local`, or a
single-token `name_norm`) carry **no** surname and attach freely, exactly as the guard's
own docstring always intended.

| | before | after |
|---|---|---|
| name↔email/handle bridge merges | **0** | **176** |
| people with an email and >2 surface forms | **0 / 3,207** | **37** |
| max surface forms on one person | 5 (case variants) | **12** |
| multi-surname clusters | 0 | **0** ✅ guard intact |

`Karthik Iyer` now resolves name + six address forms in one node — the exact case the
brief is built around, and the opening shot of the video.

**Fixed an embedded header leak in the gmail extractor.** Shaurya's `_clean_header_name`
strips a *leading* From/To/Cc/Bcc label, but collapsing newlines out of a folded header
run joins two headers mid-value: `marissa.cole@redwood.ai Cc: FreightNorth Customs
Broker`. Added `_EMBEDDED_LABEL_RE` and truncate there rather than de-prefixing — the tail
belongs to the next header. **33 leaked aliases → 0.**

**The big one: `get_entity` was 11.3 seconds.** `canonical_id` is an ordinary property
with no index, and the label was unknown up front, so every lookup scanned all twelve
labels. The node id is a deterministic hash of `canonical_id` (`bolt.surrogate_id`), so
it can be computed locally and matched as a point lookup instead. Same fix applied to
`neighbors` and `facts_about`, which anchored on `canonical_id` in a WHERE clause and
therefore scanned every edge of each type.

| | before | after |
|---|---|---|
| `get_entity` | 11.3 s | **0.010 s** |
| `facts_about` (one rel type) | 0.95 s | **0.008 s** |
| `GET /api/facts` | **15.1 s** | **0.09 s** |
| `GET /subgraph` | — | 0.11 s |

That 15s was why the conflict panel appeared to be broken: it renders fine, it was just
arriving long after anyone would have given up. **Rule worth remembering: address nodes
by integer id, never by `canonical_id`.**

**UI seen rendered for the first time** (Chrome extension still won't connect; used the
CDP-based `browser-use` harness instead). Three fixes from actually looking at it:
- Header said "PEOPLE" over a number that counts every node label, and never showed
  edges at all — `/api/stats` initialised `edges` to 0 and never queried it. HydraDB
  rejects an untyped edge pattern, so the total is a sum per relationship type. Now reads
  511,958 / 6,303 entities / 7,025 aliases / **17,185 relationships**.
- Static files are served `no-store`. Chrome held a stale `app.js` across reloads, which
  during a live demo means editing the page and watching nothing change — or recording a
  stale build without noticing. No bundler, no content hashes, so the header is the only
  guard.
- Graph node captions are truncated at a word boundary (documents are titled with a whole
  sentence and overlapped everything), with more repulsion and longer edges. Full title
  kept on the node for the click-through.

**All three demo shots verified end to end:** entity resolution (Karthik Iyer, 7 forms),
conflict (Samir Patel — Bluecord now, four superseded employers struck through with dates
and gmail doc ids, red dashed edges in the graph), abstention (total annual revenue →
declines, grade reason explains what was missing).

**154 tests pass**, 1 skipped. Eval resumed at parallelism 2 so the machine stays
responsive; it was saturating at 4 and making the UI sluggish.
