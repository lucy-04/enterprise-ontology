# Project context

This file exists so Claude Code (or any agent picking this up) has the full picture without needing it re-explained. Read this before making structural decisions. Sections marked **FIXED** come from external rules and shouldn't change without checking with the project owner (Lakshay). Sections marked **FLEXIBLE** are this project's own design choices — adjust freely if something doesn't work, and just leave a short note in this file or a commit message about what changed and why.

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

## 7. Proposed architecture — FLEXIBLE, adjust as needed

Six stages, in priority order (the problem statement explicitly weights entity resolution/conflict resolution as "the hard part" — if time runs short, protect stages 3–4 over polishing stage 1 or 6):

1. **Ingest & normalize** — parse each source's document dump into one common schema: `{doc_id, source_type, timestamp, author_refs[], body, thread_id, raw_metadata}`.
2. **Extract** — batched LLM calls pull typed entity/relation candidates per doc, tagged with `source_doc_id` for provenance. Use a **fixed target ontology defined up front** (~15–20 node types, ~20–25 edge types) rather than letting the LLM invent schema per-document — open schema induction is what makes naive entity resolution worse (see GraphRAG's known issue in §4).
3. **Entity resolution** — Splink blocking + probabilistic match, then LLM adjudication only for the genuinely ambiguous confidence band, then canonical merge into one node with an `aliases[]` property (non-destructive — keep every surface form).
4. **Ontology alignment + conflict model** — map source-specific concepts onto the fixed schema; every edge carries `stated_at, ingested_at, valid_from, valid_to, source_type, confidence`. New contradicting fact from a stronger source (e.g. a Jira field) invalidates (not deletes) the old edge; genuinely ambiguous cases get flagged `contested: true` instead of silently resolved.
5. **HydraDB storage** — canonical entities as nodes, typed relations as edges with the properties above.
6. **Query agent** — text2cypher for simple lookups, `algo.SSpaths`/`MSpaths` for multi-hop (2–3 hop cap), a grade-before-answering gate that checks whether the resolved subgraph actually supports the question before answering (abstain otherwise — this is the Refract loop from §4).

## 8. Known-flexible decisions (change freely, no need to check back)

- Full 500K-doc ingest vs. a representative sample (50–100K docs) for iteration — start with a sample, batch-run the full corpus only once the pipeline is validated.
- Exact LLM choice per stage — cheap/fast model for bulk extraction, a stronger model reserved for entity-adjudication and final answer synthesis.
- Exact source-priority ordering for conflict resolution (a hard-coded table is the realistic MVP; a learned trust model is a stretch goal, see §9).
- Exact node/edge type list in the fixed ontology, as long as it stays fixed once ingestion starts (changing it mid-run means re-running extraction).
- UI/demo framework choice — anything that can show a query, an answer, and provenance clearly is fine.

## 9. Post-MVP expansion ideas (only if core requirements are solid and time remains)

- Learned trust model instead of the hard-coded source-priority table.
- Human-in-the-loop review queue for low-confidence entity merges.
- Write-back: let a user correct a wrong merge or stale fact directly in the graph.
- Leiden community detection (GraphRAG-style) layered on top for "global" thematic questions.
- Permissions-aware retrieval respecting each source's original access controls.

---

## 10. Notes for whoever (human or agent) picks this up

- Owner: Shaurya. Comfortable in Python/Java/SQL; relatively new to RAG/agentic/graph-DB terminology — prefer clear naming and plain commit messages/comments over unexplained jargon.
- Timeline is tight — check the actual current date against the Aug 20, 2026 11:59 PM PT deadline before planning a schedule; don't assume a full week is left.
- If something in §1–3 or §6 (marked FIXED) seems to conflict with what's actually in the linked repos by the time you're reading this, the live repo/hackathon page wins — these were accurate as of early-to-mid August 2026 research but the hackathon repos may have updated.
