# Project context

This file exists so Claude Code (or any agent picking this up) has the full picture without needing it re-explained. Read this before making structural decisions. Sections marked **FIXED** come from external rules and shouldn't change without checking with the project owner (Lakshay). Sections marked **FLEXIBLE** are this project's own design choices — adjust freely if something doesn't work, and just leave a short note in this file or a commit message about what changed and why.

> **New here / just been handed this file?** Read §1 (what we're building), §7 (how it works and why), then **§11 for your own track only** — §12 is the interface between the two tracks and §13 is the schedule. §2–6 are background you can read once. This is a two-person build under a 3.5-day deadline; don't read the whole thing before starting.

> ## 🚫 AGENTS: NEVER COMMIT. THIS IS ABSOLUTE.
>
> If you are Claude Code or any other agent working in this repo: **do not run `git commit`, `git push`, `git rebase`, `git reset`, `git merge`, `git checkout <branch>`, `git stash`, or anything else that creates or rewrites history.** Not once, not "just this small one", not even when a task seems to need it.
>
> Make your file changes, leave them **unstaged in the working tree**, and tell Lakshay what you changed and which files. He stages, writes the message, and commits everything himself.
>
> Read-only git is fine and encouraged — `status`, `log`, `diff`, `show`, `reflog`, `cat-file` — for reporting the current state.
>
> If something genuinely seems to require a commit, **ask first and wait for an answer.**

> **Two companion files, kept current — read both before doing anything:**
> - **`PROGRESS.md`** — what's actually built right now, what broke and why, decisions already made. **Start every session here.** This file (`CLAUDE.md`) says what we intend; `PROGRESS.md` says where we actually are. When they disagree, `PROGRESS.md` is right.
> - **`SETUP.md`** — how to install, build and run everything, step by step. Steps are marked ✅ verified or 🔶 not-yet-run; fix them in place when you run them.
>
> If you change something structural, update `PROGRESS.md` in the same session. A future session with no memory of this conversation depends on it.

---

## 1. Original problem statement — FIXED

This is the hackathon brief, close to verbatim. Do not reinterpret it away from this.

> **Track: Enterprise context and ontology**
> Build an ontology out of real enterprise applications.
>
> You get roughly half a million documents drawn from nine different sources: Slack, Gmail, Linear, Google Drive, HubSpot, Fireflies, GitHub, Jira and Confluence. They arrive with all the noise an actual company has, including misfiled documents, near duplicates and statements that flatly contradict each other.
>
> Your job is to turn that into a clean, queryable ontology in HydraDB and then answer questions ranging from simple lookups to multi-hop reasoning, conflict resolution, and correctly recognizing when the answer just is not in there.
>
> Extraction is the easy part now that LLMs are cheap. The hard part is entity resolution and ontology alignment: deciding that "Sam", "@soham" and "S. Ratnaparkhi" are one person, and figuring out which of two contradictory statements to trust.

This is Track 1 of **Hack Hydra**, an open-source hackathon run by HydraDB.

- Hackathon window: **Aug 12–20, 2026**. Submissions close Aug 20, 11:59 PM PT.
- **No commits before Aug 12, 2026 are allowed in the submitted repo.** Start this repo fresh — judges read commit history.
- Team size: 1–4 people.
- Prizes: $5,000 Grand Champion / $3,000 Runner-up / $1,500 Third / $500 "Best Use of HydraDB" (separate award — see §5).
- Judging criteria: technical execution, use of HydraDB and graph-native approaches, product completeness/usability, quality of results, originality.

## 2. What "done" looks like

A complete submission = a public GitHub repo (with README, setup instructions, an explicit explanation of how HydraDB is used, third-party attribution, and an OSS license) + a 3-minute-max demo video + the official submission form. See §6.

The system needs to be able to answer, against data actually ingested into HydraDB:
1. Simple lookup questions.
2. Multi-hop reasoning questions (answer requires connecting 2+ entities/documents).
3. Conflict-resolution questions (two sources disagree — system should surface which is current/trusted, not silently pick one).
4. "Not in the data" questions (system should say so, not hallucinate).

---

## 3. Datasets — FIXED (what they are), FLEXIBLE (how much of each you actually use)

### 3.1 EnterpriseRAG-Bench
Repo: https://github.com/onyx-dot-app/EnterpriseRAG-Bench (MIT license)
This is both the **raw corpus to ingest** and the **eval harness to score yourself against**.

- ~500,000 documents, synthetic company "Redwood Inference." Breakdown:

  | Source | ~Docs |
  |---|---|
  | Slack | 275,000 |
  | Gmail | 120,000 |
  | Linear | 35,000 |
  | Google Drive | 25,000 |
  | HubSpot | 15,000 |
  | Fireflies (meeting transcripts) | 10,000 |
  | GitHub (PRs/comments) | 8,000 |
  | Jira | 6,000 |
  | Confluence | 5,000 |

- **Download**: latest GitHub release or HuggingFace (`onyx-dot-app/EnterpriseRAG-Bench`). Two options:
  - `all_documents.zip` — everything, full directory structure.
  - `<source_type>_slice_<N>.zip` — flat zips, ≤5,000 docs each, per source. **Use these for sampling/dev iteration** — pull 1–2 slices per source instead of the full corpus while building.
- **Document format**: plain `.txt` files, filename prefixed `dsid_<sha>_<semantic-name>`. File content = title on first line, then content fields. Directory structure mirrors source type.
- **Questions**: `questions.jsonl`, 500 questions across 10 categories (counts below are from the repo's quickstart.md — double-check against the live file since versions may drift slightly):

  | Category | ~Count | Maps to problem-statement requirement |
  |---|---|---|
  | Basic | 200 | simple lookup |
  | Semantic | 100 | simple lookup, paraphrased |
  | Intra-Document Reasoning | 50 | multi-hop (within one doc) |
  | Project Related | 40 | multi-hop (across docs) |
  | Constrained | 30 | multi-hop + disambiguation |
  | Conflicting Info | 20 | **conflict resolution** |
  | Completeness | 10 | multi-hop, must find all relevant docs |
  | Miscellaneous | 20 | noise robustness |
  | High Level | 10 | multi-hop, no single ground-truth doc |
  | Info Not Found | 20 | **abstention** |

  There's also `extra_questions.jsonl` (100 metadata-dependent questions) — optional, excluded from the official leaderboard, only bother with these if core categories are solid and time remains.

- **Answer format** — write your system's answers as JSONL, one line per question:
  ```json
  {"question_id": "qst_0001", "answer": "Your answer text...", "document_ids": ["dsid_abc", "dsid_def"]}
  ```
  Need at least one of `answer` / `document_ids` per line; both are needed for full scoring.

- **Scoring**: `pip install -r requirements.txt`, set `LLM_PROVIDER`/`LLM_API_KEY` env vars, then:
  ```bash
  python -m src.scripts.answer_evaluation.metrics_based_eval --answers-file answer_evaluation/answers.jsonl
  ```
  Reports 4 metrics: Correctness, Completeness, Document Recall, Invalid Extra Documents. Use `--parallelism N` and `--resume` for iteration speed. Results go to `answer_evaluation/results.json` — **this is the results table for the README/demo.**

### 3.2 Salesforce HERB
HF: https://huggingface.co/datasets/Salesforce/HERB (CC-BY-NC-4.0, research use — fine for a hackathon submission, just don't build a commercial product on it verbatim)
Paper/code: https://github.com/SalesforceAIResearch/HERB

This is a **secondary, smaller stress test specifically for entity resolution** — not more corpus to bulk-ingest.

- Structure:
  ```
  data/
  ├── metadata/{customers_data.json, salesforce_team.json, employee.json}
  └── products/{TrendForce.json, ContextForce.json, ...}
  ```
- Each product file has a `team` (list of `eid_...` employee IDs), a `customers` list, and `artifacts` (Slack messages, meeting transcripts, docs, PRs, and pre-written answerable + unanswerable questions).
- **Important, explicit in the dataset card**: for RAG-style evaluation, do **not** read `team`/`customers` fields directly to answer questions — those are oracle-only fields. The system is meant to *infer* who's on a team from the artifacts (Slack messages, transcripts) instead, exactly like the "Sam/@soham/S. Ratnaparkhi" problem from the brief. Use this as a direct, small-scale check that entity resolution actually works, separate from the big EnterpriseRAG-Bench run.

---

## 4. Proven systems to lift from — FLEXIBLE (implementation), FIXED (the instruction not to rebuild these from scratch)

Don't hand-roll what these already solve. Adapt, don't reinvent:

| System | License | Repo | What to take |
|---|---|---|---|
| **Onyx** | MIT | github.com/onyx-dot-app/onyx | Same team as EnterpriseRAG-Bench; has document-normalization conventions for Slack/Gmail/GitHub/Drive/Confluence/Jira/Linear-shaped data. Borrow the per-source document schema shape instead of inventing one. |
| **Neo4j LLM Graph Builder** | Apache-2.0 | github.com/neo4j-labs/llm-graph-builder | Working LangChain backend for schema-guided LLM extraction into a Cypher graph DB, with chunk→entity provenance already modeled. HydraDB speaks Bolt + an OpenCypher subset, so this points at HydraDB with a connection-string swap, not a rewrite. |
| **Splink** | MIT | github.com/moj-analytical-services/splink | Unsupervised Fellegi-Sunter probabilistic record linkage. Links ~1M records in ~1 min on a laptop, zero training data required. **This is the actual answer to entity resolution** — use it for blocking + match scoring before any LLM adjudication step. |
| **Microsoft GraphRAG** | MIT | github.com/microsoft/graphrag | Proven Leiden community-detection + hierarchical summarization for multi-hop/"global" questions. Known, openly-tracked weakness: naive exact-string-match entity resolution (see its own issues #1718, #847) — which is exactly the gap Splink fills. Worth citing this contrast explicitly in the README. |
| **Graphiti** (pattern only, not the library itself) | Apache-2.0 | github.com/getzep/graphiti | Targets Neo4j/FalkorDB, so don't run it directly — but replicate its **bi-temporal edge pattern**: a contradicted fact is invalidated (`valid_to` set), never overwritten, with a provenance/source pointer kept. This is the direct mechanism for the "which of two contradictory statements to trust" requirement. |
| Shaurya's own **Refract** design (prior project, not a repo) | — | — | The grade → rewrite/retry → abstain loop already scoped for that project carries over almost unchanged as the query-time abstention gate here — same discipline, now gating a graph traversal instead of a retrieved chunk list. Track false-confidence rate as a headline metric, same as before. |

---

## 5. HydraDB — FIXED (must be used meaningfully), FLEXIBLE (exact query patterns)

Repo: https://github.com/hydra-db/hydradb (AGPL-3.0 — this project uses it as a client over Bolt/HTTP, not statically linked, which is the normal safe case; state this project's own license clearly regardless, per submission rules)

- Object-store-native graph DB, written in Rust. Speaks an **OpenCypher subset** for queries, and **Bolt** (Neo4j-driver compatible) for connections — meaning standard Neo4j Python/JS drivers, and tools built for Neo4j (like the LLM Graph Builder above), work against it with just a connection-string change.
- Native bounded-path procedures — **use these directly for multi-hop questions instead of hand-rolled BFS**:
  ```cypher
  CALL algo.MSpaths({
    sourceLabel: 'Entity', sourceProperty: 'name',
    sourceValues: [...], targetValues: [...],
    pairwise: true, relTypes: ['RELATES'], relDirection: 'both',
    maxLen: 3, pathCount: 5, resultLimit: 100
  }) YIELD path RETURN path
  ```
  `algo.SPpaths` (one source→one target) and `algo.SSpaths` (one source, many targets) are also available.
- **Local dev — no cloud account needed**:
  ```bash
  git clone https://github.com/hydra-db/hydradb.git && cd hydradb
  # requires Rust 1.91+, libcypher-parser, SuiteSparse GraphBLAS — see repo README for OS-specific setup
  just smoke   # sanity check
  # then run a local node with CLOUD_PROVIDER=local (see repo README "Run a local server")
  ```
- The **"Best Use of HydraDB" $500 award** is judged separately and explicitly rewards "a particularly strong graph data model," "a novel retrieval or reasoning approach," and "an interesting use of relationships, traversal, or context." The bi-temporal edge model (§4, Graphiti pattern) + native path procedures directly target this — call it out explicitly in the README/video, don't bury it.

---

### 5.1 What HydraDB's Cypher actually supports — VERIFIED 2026-08-18, read before designing anything

Measured against a running local node, and cross-checked against `cypher-compat.md` in the HydraDB repo. **These are parse-time rejections, not style preferences.** The `algo.MSpaths` snippet in §5 above used a `sourceValues` form that is not how the procedures are called — the verified form is below.

**Hard constraints on the data model:**

| Constraint | Consequence for us |
|---|---|
| **Node ids must be non-negative integers.** A string id is rejected: `node id property must be an integer` | `canonical_id` cannot be the graph id. Assign an integer surrogate id per entity and keep `canonical_id` as a string *property*. Track A owns this mapping in the loader (A4) and it must be stable across reloads. |
| **Property values may only be integer, float, boolean or string. No lists.** | `aliases[]`, `handles[]`, `emails[]`, `source_doc_ids[]` from §12 **cannot** be stored as node/edge properties. Two options — pick one and write it down: (a) join into a delimited string, or (b) model aliases as their own nodes linked by an edge. **(b) is more graph-native and demos better**, since "one person node with every alias hanging off it" is exactly the entity-resolution picture. |
| **`IS NULL` is not supported in `WHERE`** (nor `IN`, `CONTAINS`, `ENDS WITH`) | ⚠️ **This breaks the bi-temporal design as written.** §11 B4 says `valid_to = null` means "currently true", but you cannot query for null. Use an explicit boolean `is_current` property, or a sentinel far-future integer timestamp. **Decide before loading, or every conflict query has to be rewritten.** |
| **`CREATE` takes relationship paths, one hop each.** A bare node or a 2-hop chain is rejected | Loader writes one edge per statement, batched via `UNWIND` with a parameter. Endpoint and edge properties can be set inline in the same statement. |
| `WHERE` supports only `=, <>, <, >, <=, >=, STARTS WITH` | No `IN` for id lists — batch with `UNWIND $rows` instead. `STARTS WITH` needs a string literal or parameter. |
| `RETURN *` not supported; `WITH` is pass-through only (no aliasing or filtering) | Name every projected column. Don't plan multi-stage `WITH` pipelines. |
| Aggregates: `count`, `sum`, `avg`, `collect`; `count(*)` fine, `count(DISTINCT *)` not | Enough for the "completeness" questions. |

**What works, and works well:**

- **Labels** on nodes, in both `CREATE` and `MATCH` — `MATCH (n:Person) WHERE n.id = 42 RETURN n.name`.
- **Edge properties**, set inline at create time — this is where `stated_at`, `source_type`, `confidence`, `contested` live.
- **Variable-length paths with a required maximum**: `-[:RELATES*1..3]->`. Unbounded `*` is rejected by design.
- **The three native path procedures.** Verified working signature — note `sourceNode`/`targetNode` take **integer node ids**:
  ```cypher
  CALL algo.SPpaths({sourceNode: 101, targetNode: 105, relTypes: ['RELATES'],
                     relDirection: 'both', maxLen: 3, pathCount: 5})
    YIELD path RETURN path
  CALL algo.SSpaths({sourceNode: 101, relTypes: ['RELATES'], maxLen: 3})
    YIELD path RETURN path
  ```
  Config also accepts `sourceLabel`, `sourceProperty`, `sourceValues`, `targetLabel`, `targetProperty`, `targetValues` (setting a target label or property requires `targetValues`), plus weight/cost keys. Yieldable columns are only `path`, `pathWeight`, `pathCost`, and `RETURN` may name nothing else.
  A returned `path` includes full nodes (id, labels, properties) and relationships (id, type, src, dst, properties) — everything needed to render provenance, in one call.
- `MERGE` on id, `SET`/`REMOVE`/`DELETE`/`DETACH DELETE` after a `MATCH`, `UNWIND` batches, `UNION`, `OPTIONAL MATCH` for reads.

**Verified working local setup:** Bolt auth is the dev token as the password (`("neo4j", "local-development-token-32-bytes")`). Endpoints: Bolt 7687, HTTP 8443, admin 9090. `just db-check` exercises all of the above and must stay green.

### 5.1.1 Write and batch rules — MEASURED 2026-08-19 while building the loader (A4)

The §5.1 table above is about *reading*. Writing is narrower still, and none of it is guessable — each rule below cost a failed query to discover. All of it is encoded once in `src/graph/bolt.py`; go through those helpers rather than hand-writing Cypher.

| Rule | Why it matters |
|---|---|
| **Batched writes only work over Bolt.** The HTTP `/query` endpoint routes to the in-process shard API, which takes scalar parameters only and rejects every `UNWIND` form. | The failure message talks about row execution, not batching, so it sends you debugging the statement when the statement is fine and the *transport* is wrong. `cypher-compat.md` says this explicitly; it is easy to miss. |
| **A bare node cannot be created.** `CREATE (n:X {...})` and `MERGE (n:X {...})` both reject: "only one-hop edge patterns are executable". | Nodes can only be made by the `UNWIND ... MERGE` upsert form, or as the endpoint of an edge. |
| **Vertex upsert form is fixed**: `UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Label, n.p = row.p` — with **exactly one** SET label. | Two labels are rejected, so a node cannot carry both a generic `:Entity` marker and its real type. We use the real type and keep `entity_type` as a property. |
| **Relationship batch form is fixed**: `UNWIND $rows AS row MATCH (s:L1 {id: row.s}), (d:L2 {id: row.d}) CREATE (s)-[:REL {id: row.i, ...}]->(d)`. Both endpoints need exactly one label; the rel type is a literal; **every** edge property must read from the row map. | A literal like `{is_current: true}` is rejected. Rows therefore have to be grouped by `(src_label, rel_type, dst_label)` — that triple is the unit of work. |
| **Never project `e.id` in a RETURN.** | `id` is the relationship's reserved identity. Selecting it fails with **"unbound variable e"**, which reads like a scoping bug somewhere else entirely — this one wasted the most time by far. Store a separate `edge_id` string property and project that. |
| **Batch cap is 1024 items** (`client_query_batch_items ... exceeds limit 1024`). | Loader uses 500. Measured throughput at that size: ~8,800 nodes/s and ~1,600 edges/s. |
| **`MATCH (n) WHERE n.prop = $v`** with no label is rejected: "node-only MATCH requires an id, label, or property". | Any "find by name" is one query per label. |
| **`algo.MSpaths` addresses nodes by string property** — `sourceValues` must be a list of strings, and it requires `sourceLabel`/`targetLabel` alongside `sourceProperty`/`targetProperty`. `SPpaths`/`SSpaths` take integer `sourceNode`/`targetNode` instead. | And a list parameter is rejected outside `UNWIND` ("composite parameter is only supported as an UNWIND input"), so those value lists have to be inlined into the query text. |
| `relDirection` is `'incoming'` / `'outgoing'` / `'both'`. | `'out'` is rejected. |

**Three consequences already baked into the data model** (`src/graph/load.py`):

1. **Integer surrogate node ids** are a 62-bit hash of `canonical_id` — stable across reloads with no mapping table to persist, and the loader aborts if two entities ever collide rather than silently merging them.
2. **Alias nodes, scoped to their owner.** Surface forms become `:Alias` nodes linked by `HAS_ALIAS` (no list properties exist). They are *not* shared between entities: a shared "ben" node would let two different Bens be joined by a 2-hop path, inventing a relationship. `HAS_ALIAS` is also excluded from default traversals for the same reason.
3. **`is_current` boolean + far-future sentinel** (`4102444800`) for `valid_to`, because there are no null properties and `IS NULL` is unqueryable. `GraphClient` converts the sentinel back to `None` so `Edge.is_current` stays truthful.

---

## 6. Submission requirements — FIXED

Three things, all due Aug 20, 2026, 11:59 PM PT:

1. **Public GitHub repo** containing: complete source, README, setup/run instructions, an explicit explanation of how HydraDB is used, required env/dependency info, third-party attribution (Onyx, Splink, etc. — see §4), and an open-source license file. First commit must be dated on/after Aug 12, 2026.
2. **Demo video, ≤3 minutes.** Cover: the problem, what was built, a working demo, and how/why HydraDB was used. Anything past 3 minutes may not be reviewed.
3. **Official submission form** — project name, description, problem addressed, what was built, deployed link (if any), how HydraDB was used, tech stack, team + contributions, repo link, video link.

Suggested demo script (see §7 for build-order framing of the same idea):
1. Show 2–3 raw docs where the same person has different names across sources.
2. Show the resolved graph node with all names merged as aliases.
3. Ask live: one simple lookup, one conflict question (system shows both sides with provenance, not a silent pick), one "not found" question (system correctly abstains).
4. End on the EnterpriseRAG-Bench results table (§3.1) — concrete numbers beat a code tour.

---

## 7. Architecture — SETTLED (Aug 17), implementation details FLEXIBLE

**Two retrieval layers over the same corpus, with a router picking between them per question.**

```
ALL ~500K docs ──▶ normalize ──▶ SQLite FTS5 (keyword) + local embeddings (vector)   ← Layer 1
                       │
                       └──▶ rule-based extract ──▶ Splink entity resolution ──▶ HydraDB graph  ← Layer 2
                                (+ LLM only for the genuinely ambiguous cases)

question ──▶ router ──▶ lookup      → Layer 1, hydrate entities from Layer 2
                        multi-hop   → Layer 2 (algo.SSpaths / algo.MSpaths)
                        conflict    → Layer 2 (bi-temporal edges, show both sides)
                        no evidence → abstain
```

### 7.1 Why two layers

The scorer measures two different things — did you find the right documents, and did you reason correctly over them — and those want different machinery.

- **Layer 1 (search index)** answers "which documents are relevant?" with no LLM involved: keyword search plus vector similarity, fused by reciprocal rank. Cheap enough to cover **all 500K docs**, which is what protects the Document Recall metric and the ~300 Basic/Semantic lookup questions.
- **Layer 2 (ontology graph in HydraDB)** answers what search structurally cannot: multi-hop questions where no single document contains both halves of the answer, conflicts where two sources disagree and both need surfacing with dates, and abstention where the absence of any connecting path is itself the evidence.

Neither layer alone covers the question mix. Together they do.

### 7.2 Why extraction is rule-based, not LLM-based — IMPORTANT, this is the key constraint

We are on **free-tier LLM access only** (~1K–15K requests/day depending on provider). An LLM cannot read 500K documents. It does not need to.

Much of this corpus is structured enough to parse deterministically, so a **per-source parser recovers a large share of entities and relations for free, at full corpus scale.** That is the primary extractor.

> ⚠️ **Corrected 2026-08-17 after reading the real files.** An earlier version of this section claimed "Jira/Linear carry assignee and reporter fields." **That is false.** Jira documents are prose. The accurate, measured per-source picture is in §7.4 — read that before writing any extractor, and do not trust the generic claim above over the specific table below.

The scarce LLM budget is spent only where rules genuinely cannot help:

| Use | Approx. calls | Model tier |
|---|---|---|
| Adjudicate ambiguous entity merges (Splink mid-confidence band) | 200–500 | strong |
| Adjudicate contradictions the source-priority table can't settle | 100–300 | strong |
| Final answer synthesis (500 questions + retries) | ~800 | strong |
| *Optional:* LLM extraction over prose-heavy sources (Fireflies, Confluence) | budget-capped, resumable, cached | cheap |

Core usage stays under ~2K calls. This also lines up with the brief's own framing — *"extraction is the easy part… the hard part is entity resolution and ontology alignment"* — so spending the scarce resource on the hard part is the correct call, not a compromise. **Say this explicitly in the README**; it reads as a deliberate design choice, which it is.

Embeddings run locally (sentence-transformers on Apple MPS), so vector indexing is free too.

### 7.4 Actual document formats — measured, not assumed

Read from real files in release v1.0.0 on 2026-08-17. **This table is the specification for Track B's rule-based extractors (B1).** Every file follows the same outer shape — filename `dsid_<32hex>__<semantic-slug>.txt` (note the **double** underscore), title on line 1, then content — but what follows the title varies enormously by source.

| Source | Format after the title line | Rule-extractable? |
|---|---|---|
| **gmail** | Real RFC-style headers: `From: Name <email>`, `To:`, `Cc:`, `Date:`, `Subject:`, then body. Measured: 157/200 sampled docs carry the full header block; 346 `From:` lines across those 200, so many are multi-message threads. | ✅ **Excellent.** Names, emails, timestamps and thread structure come out directly. Highest-confidence source for Person nodes. |
| **slack** | Line 1 is the channel name, then one message per line as `speaker: text`. **Measured across 20 docs: only ~2 use the `handle (team):` form; ~18 use a bare `speaker:`.** Speaker shapes vary widely — `jin:`, `Maria R.:`, `lena-sales:`, `maria.s:`, `Alex K:`, `oncall-ryan:`, plus bots (`incident-bot:`, `deploy-bot:`). `@mentions` appear inside messages. Code fences inline. | ✅ **Excellent**, but the regex must accept the bare form or you lose ~90% of speakers. Filter prose labels (`Requirements:`, `Impact:`) or they become people. |
| **jira** | **Prose narrative — no assignee/reporter/status fields.** But it carries recurring patterns: speaker lines `Role (Name):` and `Name (Role):` (e.g. `Support (Aisha):`, `Aisha Patel (Support):`), ticket refs `SUP-\d+` / `TRACK-\d+` / `OPS-\d+` / `DOC-\d+`, `PR #\d+`, `owner: Name`, and ISO timestamps. The ticket id is also in the filename slug. | ⚠️ **Partial.** Pattern-mine the prose; there are no fields to read. |
| **confluence** | Prose / markdown. Title, `## Section` headers or `----` underlines, occasional inline `Owner: <team>` and `Request queue: <QUEUE>`. | ❌ **Weak.** Little person/relation structure. Mostly a Layer 1 (search) source. |
| **fireflies** | Meeting summary, then action-item lines in the form `Org (Person) to <do something>` — e.g. `Redwood (Marcus) to send security package`, `Nordic Bank (Priya) to share retention language`. | ✅ **Good.** Person + org + commitment, all on one line. Same pattern family as jira/slack. |
| **hubspot** | Customer profile prose plus a **dated `Recent activity (timeline)` block**, named people with roles (`discovery call with Jordan (AE) + Maya (SE)`), and **cross-references to Fireflies meeting ids** (`Fireflies: ff_20260304_8b2f1a`). | ✅ **Good**, and see the note below on cross-source ids. |
| **github** | PR description prose: Motivation / What this PR does / Design notes / Changed areas (real file paths) / Testing / Rollout. No author field. | ⚠️ **Partial.** Strong for artifacts and components, weak for people. |
| **linear** | Title is a slug. Then Problem / Goal / Scope prose and one acceptance criterion per line. No assignee field. | ⚠️ **Partial.** |
| **google_drive** | Long-form internal docs: title, `Purpose` / `Why we run this` / `Scope` prose sections with bulleted lists. No owner or author field. | ❌ **Weak.** Like confluence — a Layer 1 source, not a graph source. |

**Cross-source join keys are the multi-hop goldmine.** HubSpot records cite Fireflies meeting ids (`ff_<date>_<hash>`), and Jira prose cites ticket ids (`SUP-`, `TRACK-`, `OPS-`, `DOC-`) and `PR #\d+`. These are deterministic, zero-LLM edges that connect *different sources* — exactly the structure "connect 2+ entities across documents" questions need, and exactly what plain search cannot follow. **Extract every such id as a first-class node and link it.** This is probably the single highest-value thing in B1.

**Two traps the normalizer already hit — don't rediscover them.**
1. *Prose labels look like speakers.* `Requirements:`, `Impact:`, `Summary:`, `Auto-summary (auto-generated):` all match a naive `^Word:` speaker regex and become fake people. `src/ingest/normalize.py` carries a `NOT_A_SPEAKER` stopword set plus a recurrence test (a real speaker either talks twice or looks like a handle). Reuse it; don't write a second one.
2. *Bots are everywhere in slack* — `incident-bot`, `deploy-bot`, `ops-bot`, `triage-bot`, `IncidentBot`, `OpsPlaybot`. They are extracted as speakers because they genuinely are, but they are **not people**. Give them their own node type in the ontology or they will pollute entity resolution and every "who did X" answer.

**One pattern family covers four sources.** `handle (team):` in slack, `Role (Name):` / `Name (Role):` in jira, `Org (Person) to …` in fireflies, and `Name (Role)` inline in hubspot are all the same `X (Y)` shape. Write one well-tested parser with per-source configuration rather than four separate ones.

Three consequences that shape the whole build:

1. **Slack and Gmail are the backbone of the entity graph**, not Jira. They are also the two largest sources (~275K and ~120K docs), so the graph gets its people and relationships from cheap, reliable parsing at scale.

2. **Confluence is the top question source (114 questions) but the worst graph source.** That is fine and it is why the two-layer design exists: Confluence questions are mostly content lookups, which Layer 1 answers well. Do not burn LLM budget trying to force Confluence into the graph.

3. **The entity-resolution problem is naturally present and easy to demo.** Real observed variants: `Support (Aisha):` vs `Aisha Patel (Support):`; `Maya` vs `Maya Chen`; `Priya` vs `Priya Nair`. And in Slack the same handle appears under different teams — `bob (eng-runtime)` vs `bob (sre)`, `maria (oncall)` vs `maria (on-call)` — which is simultaneously an alias problem *and* a conflicting-fact problem about team membership. Use a real pair like this in the demo video instead of the brief's hypothetical Sam/@soham example.

### 7.3 Pipeline stages

Priority order — if time runs short, protect stages 3–4 over polishing 1 or 6:

1. **Ingest & normalize** — parse each source's dump into one common schema (see §12 for the exact contract). No LLM.
2. **Extract** — rule-based per-source extractors emit typed entity/relation candidates, every row tagged with `doc_id` for provenance. Against a **fixed ontology defined up front** (~15–20 node types, ~20–25 edge types), never LLM-invented per document — open schema induction is exactly what makes naive entity resolution worse (see GraphRAG's known issue in §4).
3. **Entity resolution** — Splink blocking + probabilistic match, LLM adjudication only for the ambiguous confidence band, then canonical merge into one node with an `aliases[]` property. **Non-destructive — keep every surface form.**
4. **Ontology alignment + conflict model** — every edge carries `stated_at, ingested_at, valid_from, valid_to, source_type, confidence`. A contradicting fact from a stronger source invalidates (never deletes) the old edge, setting `valid_to` and `superseded_by`. Genuinely ambiguous cases get flagged `contested: true` rather than silently resolved.
5. **HydraDB storage** — canonical entities as nodes, typed relations as edges with the properties above.
6. **Query agent** — router picks the layer; `algo.SSpaths`/`MSpaths` for multi-hop (2–3 hop cap); a grade-before-answering gate that checks whether the retrieved evidence actually supports the question before answering, and abstains otherwise (the Refract loop from §4).

## 8. Decisions already made — don't re-litigate these

Settled Aug 17 with the project owner. Everything here was previously open; these are now closed.

| Decision | Choice | Why |
|---|---|---|
| Corpus scope | **Full ~500K docs**, not a sample | Rule-based extraction is free, so there's no reason to sample. Layer 1 covers everything. |
| LLM access | **Free tiers only**, provider-agnostic | Drives everything in §7.2. Route through one adapter so the provider can be swapped. |
| Bulk extraction | **Rule-based parsers**, LLM only for ambiguity | See §7.2. |
| Keyword index | SQLite FTS5 | Zero extra dependencies, fine at 500K rows. |
| Vector index | Local sentence-transformers (`bge-small-en-v1.5`) + hnswlib/FAISS | Free, runs on MPS. |
| Intermediate storage | Parquet on disk (+ DuckDB for staging) | Lets the two tracks hand work off without a shared service. |
| Demo surface | **Web UI with node-link graph view** | Judged on product completeness; this is also the video. |
| Team split | 2 tracks, see §11 | Infra vs. AI, meeting only at the §12 contracts. |

Still genuinely flexible: exact source-priority ordering for conflicts (hard-coded table is the MVP; learned trust model is a stretch goal, §9), exact node/edge type list (as long as it's frozen before the full extraction run), UI framework specifics.

## 9. Post-MVP expansion ideas (only if core requirements are solid and time remains)

- Learned trust model instead of the hard-coded source-priority table.
- Human-in-the-loop review queue for low-confidence entity merges.
- Write-back: let a user correct a wrong merge or stale fact directly in the graph.
- Leiden community detection (GraphRAG-style) layered on top for "global" thematic questions.
- Permissions-aware retrieval respecting each source's original access controls.

---

## 10. Notes for whoever (human or agent) picks this up

- **Team of two.** Lakshay owns Track A (infra/backend), Shaurya owns Track B (AI/ML). See §11 for the full split. *(Names: confirm this mapping is right — earlier drafts of this file were inconsistent about who's who.)*
- Lakshay is comfortable in Python/Java/SQL and is relatively new to RAG/agentic/graph-DB terminology — prefer clear naming and plain commit messages/comments over unexplained jargon. Explain a term the first time you use it.
- Timeline is **very** tight: this was written Aug 17, deadline is Aug 20 11:59 PM PT — about 3.5 working days. Check the current date before planning anything; don't assume a full week is left. See §13.
- If something in §1–3 or §6 (marked FIXED) seems to conflict with what's actually in the linked repos by the time you're reading this, the live repo/hackathon page wins — these were accurate as of early-to-mid August 2026 research but the hackathon repos may have updated.

---

## 11. Team split — two parallel tracks

The dividing line is: **Track A moves and stores data, Track B decides what it means.** Each track reads only its own column plus §12. After night one, neither person blocks the other.

### Track A — Lakshay (infra / backend / fullstack)

Corpus acquisition, normalization, both search indexes, HydraDB build+load+query, the API, the UI, the eval runner.

**A0. Repo scaffold + HydraDB spike — night one, blocking everything.**
`src/{ingest,index,graph,api,ui}`, `pyproject.toml` (uv), `.env.example`, `justfile`. Then build HydraDB locally per its README (Rust 1.91+, libcypher-parser, SuiteSparse GraphBLAS), `just smoke`, run a node with `CLOUD_PROVIDER=local`, and prove the Neo4j Python driver connects over Bolt and `algo.SPpaths` returns a path on a 3-node toy graph.
*If the build isn't working after ~90 minutes:* develop against local Neo4j (same Bolt driver, same Cypher — a connection-string swap) and keep debugging HydraDB in the background. **Final numbers and the demo must run on HydraDB.** Neo4j is a dev unblock only and does not get described as part of the architecture.

**A1. Corpus acquisition.** `all_documents.zip` from the EnterpriseRAG-Bench release (fall back to per-source slices if the full zip is slow), plus `questions.jsonl`. Verify per-source counts against §3.1 and record the actuals — versions drift.

**A2. Normalizers, one per source.** Format is title-on-first-line then `Key: value` fields. One generic parser plus nine thin per-source subclasses for that source's header names and timestamp formats. Borrow field naming from Onyx's connector conventions (§4) instead of inventing it. Emits the `NormalizedDoc` contract (§12). Idempotent and resumable; parallelize with `multiprocessing` — full 500K should take well under an hour.

**A3. Layer 1 index.** SQLite FTS5 for keyword + local sentence-transformers (`bge-small-en-v1.5`, MPS) into hnswlib/FAISS for vectors. Hybrid score = reciprocal rank fusion of both. Exposed as `GraphClient.search()`. Sanity target: for sampled Basic questions with known gold docs, the gold doc lands in the top 20.

**A4. HydraDB loader.** Read B's `entities.parquet` + `edges.parquet`, batch-write nodes and edges over Bolt using `UNWIND`. Index `canonical_id`, `canonical_name`, and alias lookup. **Must be re-runnable from scratch** (drop + reload) — B will regenerate the graph several times. Stage in DuckDB/Parquet first so a reload never re-triggers extraction.

**A5. Query layer.** Implement `GraphClient` (§12) fully — especially `paths()` over `algo.MSpaths`/`algo.SSpaths`. **Use the native path procedures; do not hand-roll BFS.** This is precisely what the "Best Use of HydraDB" award rewards (§5). `facts_about()` must return superseded edges too, because conflict answers need both sides.

**A6. FastAPI service.** `POST /ask` (delegates to B's router), `GET /entity/{id}`, `GET /doc/{id}`, `GET /subgraph?ids=`. Every answer returns its trace object alongside it.

**A7. Demo UI.** Single page: question box → answer → the documents behind it → the resolved person node with every alias visible → conflicting facts side by side with source and date. Node-link view of the returned subgraph (Cytoscape.js or vis-network). **This is the video** — budget real time for it, don't leave it to the last hours.

**A8. Eval runner.** `just eval` — run B's router over `questions.jsonl`, write `answers.jsonl`, invoke the bench scorer with `--parallelism` and `--resume`, render `results.json` as a markdown table for the README. Include a **per-category breakdown**; the category table persuades judges far more than one aggregate number.

### Track B — Shaurya (AI / ML)

Ontology, extraction logic, entity resolution, conflict policy, router, answer synthesis.

**B0. Ontology v1 — night one, blocks A4.** ~15–20 node types, ~20–25 edge types in `ontology/ontology.yaml`, with per-source field mapping rules alongside (e.g. Jira `Assignee:` → `(Person)-[ASSIGNED_TO]->(Ticket)`). Fixed up front, not LLM-invented per document. **Frozen once the full extraction run starts** — changing it mid-run means re-extracting.

**B1. Rule-based extractors — the primary extraction path (see §7.2).** Per source, turn `raw_metadata` and body patterns into mentions + relations. Highest yield first: Jira / Linear / GitHub / HubSpot (fully structured), then Gmail headers, then Slack `@handles` and thread membership. Every emitted row carries `doc_id` — provenance is non-negotiable, the scorer checks cited documents.

**B2. LLM extraction, budget-capped.** Only for prose-heavy sources where rules fall short (Fireflies transcripts, Confluence). Structured output against the fixed ontology, one doc per call, hard cap on total calls, fully resumable, cached to disk by `doc_id` so reruns are free. **Skippable** — B1 must stand on its own.

**B3. Entity resolution — the centerpiece of the whole submission.** Splink (unsupervised Fellegi-Sunter, no training data needed) over the mentions table. Block on normalized surname / email local-part / handle stem. Compare on name string distance, email, handle, and co-occurrence context. Three bands: high → auto-merge, low → keep separate, **middle band → LLM adjudicates** with context snippets from both sides. Merge is non-destructive: every surface form survives in `aliases[]`. The brief's own `Sam` / `@soham` / `S. Ratnaparkhi` case must demonstrably work — it's the first thing shown in the video.

**B4. Conflict + bi-temporal model.** Group edges by `(src, rel_type, dst-type)`, detect contradictions, apply the Graphiti pattern (§4): a contradicted edge is **invalidated, never deleted** — set `valid_to`, point `superseded_by` at the winner. Winner chosen by a hard-coded source-priority table (structured system-of-record fields beat chat chatter) with recency as tiebreak. Where priority and recency disagree, or sources are peers, set `contested: true` and let the answer present both sides instead of silently picking one.

**B5. Router + abstention gate.** Classify the question (lookup / multi-hop / conflict / aggregate), pick the layer, retrieve, then **grade before answering**: does this evidence actually support an answer? If not → one rewrite/retry → then abstain. The 20 "Info Not Found" questions are free points most teams lose by hallucinating, and the grade step protects Correctness on every other category too. Track false-confidence rate as a headline metric.

**B6. Answer synthesis.** Emit `answer` text plus `document_ids`. **Cite only documents that genuinely contributed** — the scorer explicitly penalizes invalid extra documents, so padding the citation list costs points. For conflicts, state the current answer *and* the superseded one, each with source and date.

**B7. HERB spot-check (§3.2).** Small separate entity-resolution sanity test. Infer team membership from artifacts only; **never read the oracle `team`/`customers` fields**. "Our ER recovers N% of true team membership without oracle fields" is a strong, concrete README claim.

---

## 12. Contracts between the two tracks — FIXED once ingestion starts

Everything crosses the track boundary as Parquet files on disk, plus one Python client. **Neither track imports the other's internals.** Agree these on night one, before writing pipeline code; changing one mid-run costs a re-run.

### A → B: `data/normalized/{source}/part-*.parquet`
```
doc_id         str        # dsid_<sha>, parsed from the filename
source_type    str        # slack|gmail|linear|drive|hubspot|fireflies|github|jira|confluence
title          str        # first line of the file
body           str
timestamp      datetime?  # parsed from header fields where present
author_refs    list[str]  # raw surface forms from headers — NOT resolved
mention_refs   list[str]  # raw surface forms found in body (@handles, emails, Capitalized Names)
thread_id      str?
path           str        # original file path, for debugging
raw_metadata   dict       # every parsed `Key: value` header line, verbatim
```

### B → A: `ontology/ontology.yaml`
Node types, edge types, per-source field→edge mapping rules. Owned by B; read by A's loader for index and constraint setup.

### B → A: `data/candidates/mentions.parquet`
```
mention_id, doc_id, source_type, surface_form, entity_type,
context_snippet, extractor(rule|llm), confidence, timestamp
```

### B → A: `data/candidates/relations.parquet`
```
relation_id, src_mention_id, dst_mention_id, rel_type, doc_id, source_type,
stated_at, evidence_snippet, extractor, confidence
```

### B → A: `data/resolved/entities.parquet`
```
canonical_id, entity_type, canonical_name, aliases list[str],
handles list[str], emails list[str], mention_count, source_types list[str]
```
plus `data/resolved/clusters.parquet` — `canonical_id, mention_id, match_probability, method(exact|splink|llm)`.

### B → A: `data/graph/edges.parquet` — post-conflict-pass, this is what actually gets loaded
```
edge_id, src_canonical_id, dst_canonical_id, rel_type,
stated_at, ingested_at, valid_from, valid_to(null = still current),
source_type, source_doc_ids list[str], confidence,
contested bool, superseded_by(edge_id|null)
```

### A → B: `src/graph/client.py` — `GraphClient`
Also exposed over HTTP so B can develop against A's running server rather than a local copy.
```python
search(query, k=20, sources=None)                  -> list[DocHit]      # Layer 1, hybrid
get_docs(doc_ids)                                  -> list[NormalizedDoc]
find_entity(name_or_alias, type=None)              -> list[Entity]      # alias-aware
neighbors(cid, rel_types=None, at_time=None, include_invalid=False) -> list[Edge]
paths(src_ids, dst_ids, max_len=3, rel_types=None) -> list[Path]        # wraps algo.MSpaths/SSpaths
facts_about(cid, rel_type)                         -> list[Edge]        # incl. superseded, for conflicts
cypher(query, params)                              -> rows              # escape hatch
```

### B → A: `answer_evaluation/answers.jsonl`
`{"question_id", "answer", "document_ids"}` per the bench spec (§3.1), plus a sidecar `traces.jsonl` — route taken, docs retrieved, subgraph, conflicts found, grade decision — which A's UI renders.

---

## 13. Schedule — written Aug 17, deadline Aug 20 11:59 PM PT

| When | Track A (Lakshay) | Track B (Shaurya) | Gate to clear |
|---|---|---|---|
| **Aug 17 eve** | A0 scaffold + HydraDB spike | B0 ontology v1 | §12 contracts frozen; HydraDB connects (or fallback declared) |
| **Aug 18** | A1 download, A2 normalizers, A3 index | B1 rule extractors on a 5K sample | Normalized Parquet exists; extractors emit valid mentions |
| **Aug 19 AM** | A4 loader, A5 query layer | B3 Splink ER at full scale | Graph loaded in HydraDB; `paths()` returns real multi-hop paths |
| **Aug 19 PM** | A6 API, A7 UI | B4 conflicts, B5 router | End-to-end: question in → answer + provenance out |
| **Aug 20 AM** | A8 eval run + README | B6 tuning against the category breakdown | Full `results.json` exists |
| **Aug 20 by 18:00 PT** | Record video, submit form | Final tuning, freeze | **Submitted with 6h buffer** |

**Hard rule:** README and video are done by Aug 20 midday, not at 11 PM. A working system nobody can see scores nothing.

**If time runs short, cut in this order** (protect the top, drop from the bottom):
1. Entity resolution + conflict handling — the brief calls these the hard part
2. HydraDB path queries — the award criterion
3. Abstention gate — cheapest points on the board
4. UI polish
5. LLM extraction over prose sources (B2)
6. HERB spot-check (B7)

---

## 14. Working in parallel without merge conflicts — READ BEFORE YOUR FIRST COMMIT

Two people, three days, one repo. The task split in §11 is only half the job; this section is the other half. **Follow it literally.** A merge conflict in `uv.lock` at 2 AM on Aug 20 is a self-inflicted wound.

### 14.1 Directory ownership — strict

Every path in the repo has exactly one owner. **Do not edit files outside your column.** If you need a change in the other person's territory, message them — do not "just fix it."

| Path | Owner | Contents |
|---|---|---|
| `src/ingest/` | **A** | corpus download, per-source normalizers |
| `src/index/` | **A** | FTS5 + vector index build and search |
| `src/graph/` | **A** | HydraDB loader, `GraphClient`, Cypher/path queries |
| `src/api/` | **A** | FastAPI service |
| `src/ui/` | **A** | frontend |
| `src/eval/` | **A** | eval runner, scorer invocation, results table |
| `ontology/` | **B** | `ontology.yaml` and anything describing the schema |
| `src/extract/` | **B** | rule-based + LLM extractors |
| `src/resolve/` | **B** | Splink entity resolution, LLM adjudication |
| `src/conflicts/` | **B** | bi-temporal conflict pass |
| `src/agent/` | **B** | router, abstention gate, answer synthesis |
| `src/llm/` | **B** | provider adapter, caching, rate-limit handling |
| `src/common/` | **shared — frozen night one** | contract dataclasses, config loader, logging |
| `tests/fixtures/` | **A** creates, both read | committed sample docs (see §14.5) |
| `data/` | **gitignored** | never committed, ever |

### 14.2 Shared files — the four rules

**1. `pyproject.toml` / `uv.lock` — agree every dependency on night one, in one commit.**
Both people list what they need up front; A commits the complete dependency set once. Nobody edits it again during the build. If you genuinely need a new package mid-build: message the other person, **A commits it**, B pulls. Never both.
*If `uv.lock` conflicts anyway:* don't hand-merge it. `git checkout --theirs uv.lock && uv lock && git add uv.lock`.

**2. `justfile` — split by track, never edited jointly.**
```
justfile          # thin, ~5 lines, frozen night one — imports the other two
just/infra.just   # A only
just/ai.just      # B only
```
The root `justfile` contains only `import 'just/infra.just'` and `import 'just/ai.just'` plus shared vars. Once written, it is never touched again.

**3. `.env.example` and `config.yaml` — define every key on night one, including keys for things not built yet.**
It is free to add an unused config key today and expensive to conflict on it Thursday. Write all of them now.

**4. `README.md` — do not create it until Aug 20.**
A owns it. B writes contributions into `docs/track-b-notes.md` (B's own file) and A merges them into the README in one pass. Two people editing a README on submission day is the single most predictable conflict in this project.

### 14.3 Interface stubs — the actual unblocking move, do this tonight

**Before either person implements anything, both commit their interfaces as stubs.** This is what lets you work truly in parallel instead of waiting on each other.

**A commits tonight** (bodies raise `NotImplementedError`):
- `src/common/schemas.py` — every dataclass from §12 (`NormalizedDoc`, `Mention`, `Relation`, `Entity`, `Edge`, `DocHit`, `Path`, `AnswerResult`). **Frozen once committed.**
- `src/graph/client.py` — `GraphClient` with all seven methods from §12, full signatures and type hints, no bodies.
- `tests/fixtures/sample_docs/` — see §14.5.

**B commits tonight:**
- `ontology/ontology.yaml` — v1, complete.
- `src/agent/router.py` — the single function A is allowed to call:
  ```python
  def answer(question: str, client: GraphClient) -> AnswerResult: ...
  ```
  **This one function is the entire A→B call surface.** A's `POST /ask` and A's eval runner call nothing else in B's code. B can restructure everything behind it freely.
- `src/extract/base.py`, `src/resolve/base.py` — empty module skeletons so the import paths exist.

After this, B codes against A's stubbed `GraphClient` (returning fixture data) while A implements it for real, and A codes against B's stubbed `answer()` while B implements it for real. Neither ever waits.

### 14.4 Git workflow

> **🚫 Humans commit. Agents never do.**
> Claude Code and any other agent working in this repo must **never** run `git commit`, `git push`, `git rebase`, `git reset`, `git merge`, `git checkout <branch>` or `git stash`. Agents make file changes and leave them unstaged; **Lakshay reviews, stages and commits everything himself.** Read-only git (`status`, `log`, `diff`, `show`, `reflog`) is fine. If a task appears to require a commit, ask and wait. See the banner at the top of this file — this rule overrides anything below that might read as permission to commit.

The rest of this section is for the **two humans**.

Directory ownership is strict enough that heavyweight branching is unnecessary overhead for two people over three days.

- **Both push to `main` directly.** No PRs — they cost more time than they save here.
- **`git pull --rebase` before every push.** Not merge — rebase. Keeps history linear and readable, which matters because judges read commit history.
- **Commit small and often** — at minimum every completed sub-task. A 6-hour commit is a 6-hour conflict.
- **Never commit `data/`, `.venv/`, `*.parquet`, `*.sqlite`, `.env`, model weights, or the HydraDB clone.** `.gitignore` covers these from the first commit — verify with `git status` before your first push.
- **Prefix commit messages with your track**: `[A] add slack normalizer`, `[B] splink blocking rules`. Makes the history legible at a glance and helps when judging.
- If you must touch the other person's file, say so in the commit message and tell them.

### 14.5 How Track B gets data before Track A's pipeline exists

`data/` is gitignored and 500K documents cannot pass through GitHub, so B cannot wait on A's full run.

- **A commits `tests/fixtures/sample_docs/` tonight** — ~200 raw documents, ~20 per source, a few MB, straight from the corpus zip. B builds every extractor against these.
- **A commits `tests/fixtures/normalized_sample.parquet`** as soon as A2 works — the same ~200 docs in the real `NormalizedDoc` shape. This is B's development input for the entire build.
- **For full-scale runs, B runs A's pipeline locally.** `just fetch-data && just normalize` — it's rule-based and needs no LLM, so it costs only time. Whoever has more RAM should run the full-corpus stages.
- **Never** try to sync `data/` through git, git-lfs, or a zip in the repo.

> ⚠️ **Confirm your teammate's machine specs before Aug 18.** The dev machine here is an 8 GB M1, which is tight for the full 500K pipeline (see `PROGRESS.md` → Risks). If the other machine has 16 GB+, the heavy full-corpus stages should run there.

### 14.6 Progress tracking without conflicts

`PROGRESS.md` was originally one shared file — that is a conflict on every single session. It is now split:

| File | Who writes | What |
|---|---|---|
| `PROGRESS.md` | either, but **only the Status block** — keep edits to a few lines | current state, decisions, risks, index into the two logs |
| `progress/track-a.md` | **A only** | A's session log, issues, fixes |
| `progress/track-b.md` | **B only** | B's session log, issues, fixes |

Log your failures and dead ends, not just successes. "Tried X, failed because Y" is what stops the other person — or a fresh agent session — burning an hour rediscovering it.
