# Redwood Ontology

**An enterprise ontology built from ~512,000 messy documents, stored in HydraDB, and queried with provenance — including the ability to say "these two sources disagree" and "that isn't in the data."**

Built for [Hack Hydra](https://github.com/hydra-db/hydradb) Track 1 — *Enterprise context and ontology*.

---

## The problem

You get roughly half a million documents from nine real enterprise applications — Slack, Gmail, Linear, Google Drive, HubSpot, Fireflies, GitHub, Jira, Confluence — carrying all the noise an actual company has: misfiled documents, near-duplicates, and statements that flatly contradict each other.

The brief is explicit that extraction is no longer the hard part:

> *"The hard part is entity resolution and ontology alignment: deciding that 'Sam', '@soham' and 'S. Ratnaparkhi' are one person, and figuring out which of two contradictory statements to trust."*

So that is where this project spends its effort, and where its LLM budget goes.

## What we built

```
ALL 511,961 docs ──▶ normalize ──▶ SQLite FTS5 + local vectors        ← Layer 1: find documents
                          │
                          └──▶ rule-based extract ──▶ Splink entity   ← Layer 2: understand them
                                   resolution ──▶ HydraDB graph

question ──▶ router ──▶ lookup     → Layer 1, entities hydrated from Layer 2
                        multi-hop  → Layer 2, algo.SSpaths / algo.MSpaths
                        conflict   → Layer 2, bi-temporal edges, both sides shown
                        no evidence→ abstain
```

**Two layers, because the benchmark measures two different things.** It scores whether you found the right documents *and* whether you reasoned correctly over them, and those want different machinery.

- **Layer 1** answers *"which documents are relevant?"* with **no LLM at all** — keyword search fused with vector similarity by reciprocal rank. Cheap enough to cover all 512K documents, which is what protects Document Recall and the ~300 plain-lookup questions.
- **Layer 2** answers what search structurally cannot: multi-hop questions where no single document holds both halves of the answer, conflicts where two sources disagree and both need surfacing with dates, and abstention where *the absence of a connecting path is itself the evidence*.

Neither layer alone covers the question mix. A router picks per question.

---

## How HydraDB is used

HydraDB is the ontology store and the reasoning engine for every question that search alone cannot answer. It is not a bolt-on: **the shape of the data model was determined by measuring what HydraDB's Cypher subset actually accepts**, and three non-obvious design decisions came directly out of that.

### Native path procedures, not hand-rolled traversal

Multi-hop questions are answered by HydraDB's own bounded-path procedures rather than a BFS written in Python:

```cypher
CALL algo.SPpaths({sourceNode: 101, targetNode: 105, relTypes: ['RELATES'],
                   relDirection: 'both', maxLen: 3, pathCount: 5})
  YIELD path RETURN path
```

`GraphClient.paths()` picks between all three — `SPpaths` (one source → one target, integer node ids), `MSpaths` (many → many, addressed by string property, grouped per label pair), and `SSpaths` (one source, open-ended). A returned `path` carries full nodes *and* relationships with all their properties, so the entire provenance chain for an answer — who, via what, stated when, sourced where — arrives in a single call and renders directly in the UI.

### Three data-model decisions forced by the engine, and why they are better

Measured against a running node and written up in [`CLAUDE.md` §5.1 and §5.1.1](CLAUDE.md); encoded once in [`src/graph/bolt.py`](src/graph/bolt.py).

**1. Integer surrogate node ids.** HydraDB requires node ids to be non-negative integers, so a string `canonical_id` cannot be the identity. Rather than persist a mapping table, ids are a **62-bit blake2b hash of the canonical id** — stable across reloads with no state to keep in sync, and the loader *aborts* on a collision rather than silently merging two people.

**2. Aliases as owner-scoped nodes.** Property values may only be scalars — no lists — so `aliases[]` cannot be a property. Modelling each surface form as its own `:Alias` node linked by `HAS_ALIAS` is the more graph-native answer anyway: "one person node with every alias hanging off it" *is* the entity-resolution picture, and it draws well. The subtlety is that alias nodes are **scoped to their owner** and never shared. A single shared `"ben"` node would let two different Bens be joined by a 2-hop path, inventing a relationship that does not exist — so `HAS_ALIAS` is also excluded from default traversals.

**3. `is_current` boolean plus a far-future sentinel.** `IS NULL` is not queryable, which breaks the textbook bi-temporal model where `valid_to = null` means "still true". Validity is therefore an explicit boolean plus a sentinel timestamp (`4102444800`), which `GraphClient` converts back to `None` so `Edge.is_current` stays truthful at the API boundary.

### Writing at scale

The write path is narrower than the read path and none of it is guessable. Batched writes work **only over Bolt** (the HTTP endpoint routes to a shard API that takes scalar parameters and rejects every `UNWIND` form); a bare node cannot be created at all, only upserted via `UNWIND ... MERGE` or as the endpoint of an edge; every edge property must read from the row map, so rows are grouped by `(src_label, rel_type, dst_label)` as the unit of work; and the batch cap is 1024 items. Measured throughput at batch size 500: **~8,800 nodes/s and ~1,600 edges/s.**

One trap cost more debugging time than everything else combined and is worth stating plainly: **never project `e.id` in a `RETURN`.** `id` is the relationship's reserved identity, and selecting it fails with `unbound variable e` — an error that reads like a scoping bug somewhere else entirely. Edges carry a separate `edge_id` string property instead.

---

## The hard parts

### Entity resolution

[Splink](https://github.com/moj-analytical-services/splink) (unsupervised Fellegi–Sunter probabilistic record linkage — no training data required) over the mention table: blocking on normalised surname, email local-part and handle stem; comparison on name distance, email, handle and co-occurrence. High-confidence pairs auto-merge, low-confidence stay separate, and the **middle band is adjudicated by an LLM** with context from both sides. Merging is non-destructive — every surface form ever written survives.

**The failure this taught us, and the fix.** At 180 documents the results looked perfect. At 2,000 they were not: `Alex Chen` had accumulated **73 aliases** spanning `Alex Jenkins`, `Alex Torres` and even `Aisha Patel`, and 22 such blob-entities had absorbed **29% of all person mentions**.

Two mechanisms compounded. Union-find takes the **transitive closure**, so a handful of false-positive pairs chain an entire component together. And **bare first names act as bridges** — a Slack mention of just `Alex` legitimately pairs with `Alex Chen` *and* `Alex Jenkins`, so through that one node two different people merge.

The fix is a **surname-consistency guard**: each component tracks the distinct surnames it holds, and any union that would place two different surnames in one cluster is rejected. Mentions with no surname still attach freely, but once a cluster commits to a surname a conflicting one can never join — so the bridge chains are structurally impossible rather than merely unlikely. Pairs are processed strongest-first so clusters commit correctly, and a post-run check asserts zero multi-surname clusters.

| | before | after |
|---|---|---|
| max aliases on one person | 73 | **3** |
| blob entities (≥11 aliases) | 22 | **0** |
| cross-surname merges blocked | — | **102,953** |

Legitimate merges survive: `Karthik Iyer` ↔ `karthik_iyer@redwood.ai` still resolve to one node.

This is worth contrasting with [Microsoft GraphRAG](https://github.com/microsoft/graphrag), which does exact-string entity resolution and has the over-merging weakness openly tracked in its own issues (#1718, #847). Probabilistic linkage plus a structural guard is precisely the gap that fills.

### Conflicts are invalidated, never overwritten

Following [Graphiti](https://github.com/getzep/graphiti)'s bi-temporal edge pattern: a contradicted fact **keeps its row**, gets `valid_to` set, and points `superseded_by` at whatever replaced it. Nothing is ever deleted. The winner is chosen by a source-priority table (systems of record outrank chat) with recency as tiebreak, and where priority and recency disagree the edge is flagged `contested` so the answer presents **both sides with dates and sources** instead of silently picking one.

That is what lets the system answer *"Alex is on Eng-Oncall now; they were on Support until 15 March 2026"* — with a citation for each half.

### Knowing when to say nothing

Every answer passes a **grade-before-answering gate**: does the retrieved evidence actually support this question? If not, the query is critiqued and rewritten once — the LLM diagnoses what was missing and proposes rephrasings or sub-queries — and only then does the system abstain. The 20 `info_not_found` questions are free points that most systems lose by hallucinating.

Early measurement is encouraging — zero false-confident answers so far — but the sample is small, and the honest headline number belongs in the results table below rather than here.

---

## Why extraction is rule-based

This project runs on **free-tier LLM access only**. An LLM cannot read 512,000 documents, and it does not need to.

We measured the real file formats of all nine sources before writing a single extractor ([`CLAUDE.md` §7.4](CLAUDE.md)). Gmail carries genuine RFC headers; Slack is one `speaker: text` per line; Fireflies action items are `Org (Person) to <do something>`; HubSpot cites Fireflies meeting ids and Jira prose cites ticket ids and PR numbers. Those cross-source identifiers are deterministic, zero-LLM edges connecting *different* applications — exactly the structure multi-hop questions need and exactly what flat search cannot follow.

So per-source parsers recover the entity graph at full corpus scale, for free, and the scarce LLM budget goes only where rules genuinely cannot help: adjudicating ambiguous merges, settling contradictions the priority table cannot, grading evidence, and writing the final answer. Core usage stays under ~2K calls.

Embeddings run locally (`bge-small-en-v1.5` on Apple MPS), so vector indexing is free too.

This is not a compromise. It is the brief's own framing — extraction is easy, resolution is hard — so the scarce resource is spent on the hard part.

---

## Results

Scoring is split deliberately in two.

**Deterministic metrics cost nothing and cannot fail.** `questions.jsonl` ships `expected_doc_ids` for 470 of the 500 questions, so Document Recall and Invalid Extra Documents are exact set arithmetic. The official scorer is an LLM judge, and free tiers run out — if answer grading were the only route to a number, an exhausted quota would mean no results at all. `src/eval/score.py` computes what is deterministic first, and the LLM judge adds Correctness and Completeness on top.

<!-- RESULTS -->
> **Results table pending** — regenerate with `just eval && just results`.

Two measurements already worth stating, both obtained offline for free:

**Retrieval works; showing the model enough of the document is what mattered.** Early runs abstained on questions whose gold document had already been retrieved. The grader was being shown a 320-character keyword window per document. Measured against the benchmark's own `answer_facts` across all 470 gold documents — the mean fraction of the facts needed to answer that are actually visible in the evidence:

| evidence per document | facts visible |
|---|---|
| 320 chars | 0.241 |
| 2,000 | 0.386 |
| 4,000 | 0.495 |
| **8,000 (current)** | **0.618** |
| full body (ceiling) | 0.669 |

Short documents now pass through whole; longer ones get several windows around *different* query terms, marked discontinuous so the model cannot read across a gap. 8,000 buys 92% of the ceiling for 70% of the context cost.

**Layer 1 retrieval quality**, measured by `just recall` against the benchmark's gold document ids over the full 511,958-document index. Every gold document is indexed, so the ceiling is 1.000 and recall is not confounded with corpus coverage:

| question type | n | recall@6 | recall@12 | recall@20 |
|---|---|---|---|---|
| basic | 175 | 0.731 | 0.754 | 0.783 |
| semantic | 125 | 0.392 | 0.424 | **0.480** |
| intra-document reasoning | 40 | 0.950 | 1.000 | 1.000 |
| project related | 40 | 0.975 | 1.000 | 1.000 |
| constrained | 30 | 0.967 | 0.967 | 0.967 |
| conflicting info | 20 | 0.950 | 0.950 | 0.950 |
| completeness | 20 | 0.800 | 0.800 | 0.850 |
| miscellaneous | 20 | 0.850 | 0.850 | 0.900 |
| **overall** | **470** | **0.713** | **0.736** | **0.766** |

Two things this says. The retriever is strong everywhere except **`semantic`** — paraphrased lookups, where the wording of the question shares little vocabulary with the document. That is precisely the gap vector search closes, and vectors are the one component not currently switched on (see limitations). It is the largest single improvement still available.

And the answerer's six-document context window costs only about five points (0.713 at six documents against 0.766 at twenty), so widening *what* the model sees of each document mattered far more than showing it *more* documents — which is why the snippet work above was the change worth making.

---

## Running it

Full step-by-step setup is in [`SETUP.md`](SETUP.md). Short version:

```bash
# prerequisites: Python 3.12, uv, just, and a local HydraDB build
uv sync
cp .env.example .env          # add one LLM API key

just fetch-data               # download the corpus + questions
just normalize                # 9 sources -> data/normalized/*.parquet
just index                    # SQLite FTS5 + local vectors

just db-up                    # HydraDB on Bolt 7687 (separate terminal)
just db-check                 # verify the Cypher subset we depend on

just extract && just resolve && just conflicts    # build the ontology
just load                     # write entities + edges into HydraDB

just serve                    # UI + API on http://localhost:8000
just eval                     # run the benchmark, resumable
just results                  # render the results table
```

`just pipeline` runs the whole chain in order.

**Environment.** `LLM_PROVIDER` and `LLM_API_KEY` are the only required settings; every other key in `.env.example` has a working default. Any provider works — the adapter is provider-agnostic and all LLM calls are disk-cached by content, so re-runs are free.

**Hardware.** Everything here was built and measured on an 8 GB M1 MacBook. Every stage streams; nothing assumes the corpus fits in memory.

---

## Honest limitations

- **Layer 2 covers a sample of the corpus, not all of it.** Layer 1 indexes all 511,961 documents. The graph is built over a subset, because the post-Splink clustering step materialises its working set in memory and hits the ceiling of an 8 GB machine at ~5,000 documents. This is a memory-shape issue in our own code, not a Splink limit — Splink's blocking and prediction stay cheap. Streaming that stage is the first thing we would fix with more time.
- **Vector search is currently disabled**; Layer 1 runs keyword-only. The build and search paths are written and tested — the embedding matrix over 512K documents is simply not finished, at roughly eight hours on this hardware. This is the known cause of the weak `semantic` recall above, and the clearest remaining win.
- **Bare first names cannot always be split.** If several real people are only ever written as "Sam", with no surname, email or handle anywhere, they merge. That is the genuinely ambiguous case the brief itself acknowledges, and we would rather state it than hide it.
- **A handful of non-person tokens** still classify as people (`Request`, `ERROR`, `KVCACHE`). Low mention counts; they never reach the answer set.

---

## Repository layout

| Path | What |
|---|---|
| `src/ingest/` | corpus download, per-source normalizers |
| `src/index/` | Layer 1 — FTS5 + vector build and hybrid search |
| `src/extract/` | rule-based per-source extractors |
| `src/resolve/` | Splink entity resolution, LLM adjudication |
| `src/conflicts/` | bi-temporal conflict pass |
| `src/graph/` | HydraDB loader, `GraphClient`, Cypher and path queries |
| `src/agent/` | router, abstention gate, answer synthesis |
| `src/llm/` | provider adapter, caching, rate limiting |
| `src/api/`, `src/ui/` | FastAPI service and the demo UI |
| `src/eval/` | benchmark runner and offline scorer |
| `ontology/` | the fixed ontology — 16 node types, 23 edge types |
| `demo/questions.md` | verified demo questions, one per question type |

Design decisions and their reasoning live in [`CLAUDE.md`](CLAUDE.md); what was actually built, and everything that broke on the way, is in [`PROGRESS.md`](PROGRESS.md) and `progress/`.

---

## Team

| | |
|---|---|
| **Lakshay Tuteja** ([@lucy-04](https://github.com/lucy-04)) | Infrastructure — corpus acquisition and normalization, both search indexes, the HydraDB data model, loader and query layer, API, UI, evaluation harness |
| **Shaurya Saini** ([@Shaurya-Saini](https://github.com/Shaurya-Saini)) | AI — ontology design, rule-based and LLM extraction, Splink entity resolution, conflict and bi-temporal policy, router, abstention gate, answer synthesis |

## Third-party attribution

| Project | License | Used for |
|---|---|---|
| [HydraDB](https://github.com/hydra-db/hydradb) | AGPL-3.0 | the graph store, used as a client over Bolt |
| [EnterpriseRAG-Bench](https://github.com/onyx-dot-app/EnterpriseRAG-Bench) | MIT | corpus and evaluation harness |
| [Onyx](https://github.com/onyx-dot-app/onyx) | MIT | per-source document normalization conventions |
| [Splink](https://github.com/moj-analytical-services/splink) | MIT | probabilistic record linkage for entity resolution |
| [Graphiti](https://github.com/getzep/graphiti) | Apache-2.0 | bi-temporal edge pattern (design reference; library not used) |
| [Microsoft GraphRAG](https://github.com/microsoft/graphrag) | MIT | design reference and contrast for entity resolution |
| [Salesforce HERB](https://huggingface.co/datasets/Salesforce/HERB) | CC-BY-NC-4.0 | secondary entity-resolution stress test |

Also: [Neo4j Python driver](https://github.com/neo4j/neo4j-python-driver) (Apache-2.0), [FastAPI](https://github.com/tiangolo/fastapi) (MIT), [sentence-transformers](https://github.com/UKPLab/sentence-transformers) (Apache-2.0), [Cytoscape.js](https://github.com/cytoscape/cytoscape.js) (MIT, vendored in `src/ui/vendor/`).

## License

[MIT](LICENSE).
