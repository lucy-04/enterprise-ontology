# Track B log — AI / ML (Shaurya)

**Only Track B writes to this file.** Append-only. Log dead ends as well as wins — "tried X, failed because Y" is what stops a future session repeating it.

Track B scope: `ontology/`, `src/extract/`, `src/resolve/`, `src/conflicts/`, `src/agent/`, `src/llm/`. Task list in `CLAUDE.md` §11 (B0–B7).

---

## Start here if you're Track B and this is your first session

1. Read `CLAUDE.md` §1 (the brief), §7 (architecture and **why extraction is rule-based** — this is the constraint that shapes your whole track), then §11 Track B, §12 contracts, §14 parallel-work rules.
2. Read `SETUP.md` §2–5 and get your environment running.
3. **Your night-one deliverables** (`CLAUDE.md` §14.3) — these unblock Track A:
   - `ontology/ontology.yaml` v1 — ~15–20 node types, ~20–25 edge types, plus per-source field→edge mapping rules. Frozen once the full extraction run starts.
   - `src/agent/router.py` with `answer(question: str, client: GraphClient) -> AnswerResult` as a stub. **This one function is the entire surface Track A calls into your code** — everything behind it is yours to restructure freely.
   - `src/extract/base.py`, `src/resolve/base.py` — empty skeletons so import paths exist.
4. Work against `tests/fixtures/normalized_sample.parquet` (Track A commits it) until you can run the full pipeline locally. Don't wait on Track A's full corpus run.
5. **Tell Lakshay your machine's RAM.** The other dev machine is an 8 GB M1, which is tight for the full 500K pipeline. If yours is bigger, the heavy full-corpus stages should run on yours.

---

## Task status

| Task | Status | Notes |
|---|---|---|
| B0 ontology v1 | ✅ done | 16 node types, 22 edge types, 9 source-mapping specs + source-priority table. Validated, parses. **A4 unblocked.** |
| Night-one stubs | ✅ done | `router.answer()` (abstains cleanly, never raises), `extract/base.py` (Extractor + ontology loader), `resolve/base.py` (blocking keys). **A can build API/eval now.** |
| B1 rule-based extractors | ✅ done | All 9 sources. 1011 mentions / 772 relations from the 180-doc fixture, 11 node types + 16 edge types populated. 14 regression tests green. No LLM. |
| B2 LLM extraction (prose sources) | 🔲 not started | optional, budget-capped, skippable |
| B3 Splink entity resolution | ✅ done | Splink (probabilistic, people) + exact-key (artifacts) + name↔email bridge. 282 person/bot mentions → 194 clusters. Cross-form merges verified (Karthik Iyer ↔ karthik_iyer@…). 6 regression tests green. |
| B4 conflict + bi-temporal model | ✅ done | 772 relations → 664 canonical edges. Directional single-valued conflict detection + source-priority/recency resolution. Demo: "alex" MEMBER_OF Support→Eng-Oncall superseded (both kept). 5 regression tests green. |
| B5 router + abstention gate | ✅ done | classify→retrieve→grade→retry→abstain. Never raises; degrades on stubbed client. |
| B6 answer synthesis | ✅ done | LLM (Gemini default) + extractive fallback; cites only contributors; conflict answers show both sides. 8 agent tests green (48 total). |
| B7 HERB spot-check | ✅ done | 30 products: **87% mean recall** of true team membership from artifacts only (no oracle), 71% precision, F1 0.77; co-occurrence disambiguated 59% of ambiguous shared-name refs. 6 tests green (54 total). |

Legend: 🔲 not started · 🔨 in progress · ✅ done · ⚠️ done but shaky · ❌ blocked

---

## Session log

### 2026-08-19 — B0 + night-one stubs (session 1)
- **Goal:** clear the overdue night-one deliverables that block Track A, then start B1.
- **Done:**
  - `ontology/ontology.yaml` v1 — 16 node types, 22 edge types, per-source mapping rules for all 9 sources, and the B4 source-priority table. Respects the HydraDB Cypher limits (§5.1): aliases modeled as their own `Alias` nodes (no list props), `is_current` boolean instead of `valid_to IS NULL`, string `canonical_id` as a property not the graph id.
  - `src/agent/router.py` — `answer(question, client, question_id)` stub. Classifies + abstains for now, **never raises** (an unhandled error would zero the whole eval run). This is the entire A→B surface.
  - `src/extract/base.py` — `Extractor` base class (id minting, provenance stamping, ontology-type validation) + `load_ontology()` cached loader.
  - `src/resolve/base.py` — name normalization + blocking keys (surname / email localpart / handle stem) for Splink.
- **Key discovery:** Track A's normalizer already does heavy lifting. In `normalized_sample.parquet`, gmail has `from/to/cc/subject` in `raw_metadata`; jira/fireflies/hubspot have `roles` (people) and `cross_refs` (the ticket/PR/meeting join-keys: ENG-, SUP-, PR#, ff_) already pulled out. So B1 is lighter than §7.4's warnings imply — much of the extraction is reading parsed fields, not re-parsing prose.
- **Not committed yet** — global rule is commit only when the user asks. Files are ready; Track A can pull once committed.
- **Next:** B1 per-source extractors against `normalized_sample.parquet`, highest-value first (gmail, slack, then cross_refs edges from jira/hubspot/github).

### 2026-08-19 — B1 rule-based extractors (session 1, cont.)
- **Goal:** rule-based extraction for all 9 sources, no LLM.
- **Done:** `src/extract/{classify,sources,run}.py`. Reuses Track A's regexes + `NOT_A_SPEAKER` from `normalize.py` (per §7.4 — did not write a second speaker parser). `python -m src.extract.run` reads normalized parquet → writes `data/candidates/{mentions,relations}.parquet`. On the 180-doc fixture: 1011 mentions (person 258, document 180, team 142, ticket/project 119 each, org 76, PR 28, bot 25, meeting 23, role 22, channel 20) and 772 relations across 16 edge types. `tests/test_extract.py` (8 tests) green.
- **Key design choice:** artifact nodes (Ticket/PR/Meeting/Project/Org) use their **natural key** as surface_form (e.g. "ENG-30521"), so the same ticket cited in 5 docs becomes ONE node linking all 5 — deterministic cross-source multi-hop with zero LLM. Verified: in the fixture all 28 tickets that own a doc are also referenced elsewhere.
- **Two bugs found + fixed (regression-tested):** (1) paren parser "X (Y):" was emitting prose labels like "Steps to reproduce (staging):" as people — now requires a Capitalized proper-name side. (2) `redwood.com` (not just `redwood.ai`) was treated as external, spawning a bogus "Redwood" org 57×; now any `redwood.*` domain is internal.
- **Known gap (not a bug):** Fireflies docs carry NO `ff_` id (verified — not in filename or body), so HubSpot's `ff_` REFERENCES edges point to standalone Meeting nodes that don't join to the transcripts by id. A later fuzzy join (date + customer name) could link them; B1 stays deterministic and leaves them separate. Logged so nobody re-discovers it.
- **Aliases NOT emitted in B1:** the Entity row (built in B3) carries aliases/handles/emails as Parquet lists; Track A's loader explodes them into Alias nodes at load (the no-list-property limit is only *inside* HydraDB). So B1 emits only typed entity mentions.
- **Not committed yet.**
- **Next:** B3 Splink entity resolution — exact-key merge for artifacts, probabilistic (Splink) for people/orgs, 3 confidence bands with LLM only in the middle band.

### 2026-08-19 — B3 entity resolution (session 1, cont.)
- **Goal:** resolve mentions into canonical entities — the submission centerpiece.
- **Done:** `src/resolve/{splink_er,run}.py`. Two paths: (1) deterministic exact-key merge for artifacts (ticket/PR/meeting/project/team/org/etc.) — a ticket id is one node across all docs that cite it; documents keyed by doc_id. (2) **Splink 4.0** probabilistic Fellegi-Sunter for person/bot: blocking on surname/email-local/handle-stem, comparisons = NameComparison(JaroWinkler) + ExactMatch(email/handle). Unsupervised EM training. Bands from `resolve/base.py`: ≥0.92 auto-merge, 0.55–0.92 middle→LLM (optional), <0.55 separate. On the fixture: 282 person/bot mentions → 194 clusters, all via Splink. Writes `data/resolved/{entities,clusters}.parquet`. `tests/test_resolve.py` (6 tests) green.
- **Added a name↔email↔handle bridge** (`bridge_name_email_handle`) on top of Splink: Splink's name comparison structurally can't link "Karthik Iyer" (Slack) to "karthik_iyer@redwood.com" (Gmail) because the surface strings don't resemble each other. The bridge derives plausible email-locals from a full name (first_last, first.last, flast…) and unions on an exact local match. This is the brief's headline "Sam/@soham/S.Ratnaparkhi = one person" capability. Verified merges: Karthik Iyer, Marcus Lin, Ben Carter, Marissa Cole each now unify their name + email across sources.
- **Three bugs found + fixed (all regression-tested):**
  1. **Splink prior collapse:** seeding `estimate_probability_two_random_records_match` from email alone (only ~5% of mentions have email) drove the prior to ~0 and suppressed *every* posterior below 0.55 → 0 merges. Fixed by seeding from exact name OR email. Now ~1-in-302 prior, 143 high-confidence pairs.
  2. **pandas NaN truthiness:** pandas 2.3's new `str` dtype coerces `None`→`NaN` (a float), and `NaN` is truthy — silently breaking the bridge's "has no email" checks. Splink/DuckDB read NaN as NULL fine, but the Python bridge didn't. Fixed with an explicit `_val()` nan-guard. **General lesson: never rely on bare truthiness of a pandas cell; use `pd.isna`.**
  3. **Over-merge on shared first name:** bare "ben" as a bridge key merged Ben Carter + Ben Turner. Fixed by restricting `_name_locals` to multi-component forms only.
- **Middle band is empty on the fixture** — name matches here are either exact (high) or clearly different (low). At full corpus scale the ambiguous band appears; the LLM adjudicator (still optional) handles it then. B3 runs fully with zero LLM today.
- **Committed + pushed by Shaurya** as `5f2c91c "Feat: Create Splink resolution and Data connectors"` → merged via PR #1 (`bcd6789`). B0 + stubs + B1 + B3 are on `main`.
- **Next:** B4 conflict + bi-temporal model — operates on the resolved canonical ids to build `data/graph/edges.parquet` with `is_current`/`superseded_by` (HydraDB-safe, no `IS NULL`).

### 2026-08-19 — B4 conflict + bi-temporal edges (session 1, cont.)
- **Goal:** turn per-doc relation candidates into the final canonical `edges.parquet` Track A loads, with contradictions surfaced not silently resolved.
- **Done:** `src/conflicts/run.py`. (1) Lifts relations from mentions to canonical ids via B3 clusters and dedupes duplicate assertions of the same (src, rel, dst) into one edge that keeps every `source_doc_id`. (2) Bi-temporal conflict pass (Graphiti pattern): single-valued relations with disagreeing counterparts get a winner by the ontology `source_priority` table + recency tiebreak; losers get `valid_to` + `superseded_by` set (never deleted); genuine ties → `contested=True`, both current. On the fixture: 772 relations → 664 edges, 1 real conflict (a MEMBER_OF team change), all `is_current` expressible without `IS NULL`. `tests/test_conflicts.py` (5 tests) green. Full suite: 41 passing.
- **Demo case surfaced automatically:** `alex` MEMBER_OF `Support` (until 2026-03-15) → superseded by MEMBER_OF `Eng-Oncall` (current). Both edges kept, so an answer can say "on Eng-Oncall now, was on Support until March 2026" with the source doc.
- **Two modeling fixes (regression-tested):**
  1. **Conflict direction matters.** OWNS was grouped by source ("an owner owns one thing") → false conflicts when a team owns many pages. Split into `SRC_SINGLE` (MEMBER_OF/WORKS_FOR/HAS_ROLE/REPORTS_TO/ASSIGNED_TO — group by src) vs `DST_SINGLE` (OWNS — group by dst, a thing has one owner).
  2. **Coexistence ≠ conflict.** People genuinely belong to multiple teams; when all candidate edges tie on (source, date) there's nothing to order, so it is NOT flagged. Only an *orderable* difference (different priority or date) supersedes. This dropped a bogus 47 "contested" edges to 0 on the fixture.
- **The full Track B data flow now runs end-to-end with zero LLM:** normalize → extract (B1) → resolve (B3) → conflicts (B4) → `data/graph/edges.parquet` ready for Track A's loader.
- **Not committed yet** (this B4 session).
- **Next:** B5+B6 router + abstention + answer synthesis. **Needs a free-tier LLM provider decision from Shaurya** (Gemini / Groq / OpenRouter) for answer synthesis + middle-band adjudication. The `src/llm/` adapter and router logic can be built provider-agnostic first.

### 2026-08-19 — B5+B6 router, abstention, synthesis + LLM adapter (session 1, cont.)
- **Provider decision (Shaurya):** Gemini default; Groq + others pluggable.
- **Done:**
  - `src/llm/adapter.py` — provider-agnostic `LLM`. Default **Gemini** (google-genai); Groq/OpenRouter/Ollama via the OpenAI-compatible path (base-url switch). Disk-cached by hash of (provider, model, system, prompt) under `data/cache/llm/` so a rate-limit stop costs only time; tenacity backoff; **graceful no-key mode** (`available=False` → `complete()` returns None → callers fall back). `complete_json()` for adjudication.
  - `src/agent/classify.py` — no-LLM route classifier (lookup/multihop/conflict/aggregate) + question entity extraction (ids, quoted spans, @handles, Capitalized names).
  - `src/agent/synthesize.py` — the **grade-before-answer gate** (LLM strict grader, or heuristic offline) + answer synthesis (LLM concise+cited, or extractive fallback) + `format_edge_fact` that renders supersession/contested/provenance.
  - `src/agent/router.py` — full `answer()`: classify → retrieve (Layer 1 search + Layer 2 facts/paths, each wrapped so a NotBuiltYet client degrades) → grade → **one retry** (broaden search) → abstain, else synthesize. **Never raises** (an unhandled error would zero the eval run).
  - `tests/support/local_client.py` — a `GraphClient` double over B's own parquet (TEST INFRA ONLY; Track A owns the real client). Lets the router be exercised end-to-end before A's server exists.
  - `tests/test_agent.py` (8 tests). Full suite **48 passing**.
- **Verified end-to-end on the fixture:** "Which team is Alex on now?" → conflict route, resolves the Alex entity, surfaces MEMBER_OF facts incl. the superseded Support→Eng-Oncall change with provenance. Gibberish query → abstains with empty citations.
- **Bug fixed:** the local client used `ndarray or []` (ambiguous truth value) which `_safe()` swallowed → entities never resolved. Fixed with a `_lst()` helper. **Same lesson as B3: never rely on truthiness of a pandas/numpy cell.**
- **Known limitations (by design):** offline (no key) abstention only fires on genuine zero keyword-match, and offline answers are extractive — nuanced abstention (the 20 info_not_found) and clean prose answers are the LLM grader/synthesizer's job. Set `LLM_PROVIDER=gemini` + `LLM_API_KEY` in `.env` to activate. Also: `format_edge_fact` names the src entity but shows the dst as its id when the dst isn't in the resolved set — the GraphClient interface has no `get_entity(cid)`; if we want dst names offline, ask Track A to add id→name (or the LLM/UI resolves it). Minor.
- **Not committed yet.**
- **Track B core (B0,B1,B3,B4,B5,B6) is complete.** Remaining: B2 (optional LLM prose extraction, skippable) and B7 (HERB spot-check, cut-first). Next highest-value work is tuning with a real Gemini key + an eval run (A8 territory, but B can self-score via the local client).

### 2026-08-19 — B7 HERB entity-resolution spot-check (session 1, cont.)
- **Goal:** a separate, scorable proof that entity resolution works, on Salesforce/HERB.
- **Done:** `src/resolve/herb_check.py` — downloads HERB (huggingface_hub, into gitignored `data/herb/`), infers each product's team **from artifacts only** (Slack eid authorship + @eid mentions + PRs; meeting-transcript names), and scores against the oracle `team` field (read ONLY for scoring, never as input, per the dataset card). `tests/test_herb.py` (6 tests, no network needed — the ER core is unit-tested on synthetic data). Full suite **54 passing**.
- **Result across 30 products:** mean **recall 87%**, precision 71%, F1 0.77; co-occurrence **disambiguated 493/829 (59%)** of ambiguous shared-name references.
- **Why it's a real ER test (the honest framing for the README):** HERB has **530 employees sharing only 98 names** — "Hannah Taylor" is 10 different people. Slack tags authors by eid (easy), but transcripts name people ambiguously. We disambiguate a shared name to the right employee by co-occurrence (the candidate who also appears by eid in that product's Slack) — the same context-based resolution the main pipeline uses, exactly the "Sam/@soham/S.Ratnaparkhi" problem. README claim: *"recovers 87% of true team membership from artifacts alone, disambiguating 59% of ambiguous shared-name references."*
- **Dependency note for Lakshay:** `huggingface_hub` is imported by `herb_check.py`. It resolves today (transitive via sentence-transformers, in uv.lock) but is NOT declared in `pyproject.toml`. If we want B7 robust, add `huggingface_hub` explicitly (Track A owns pyproject per §14.2). Non-blocking — works now. Also `just fetch-herb` calls `src.ingest.fetch_herb` which doesn't exist yet (Track A); `herb_check.py` self-downloads so it doesn't depend on that.
- **Not committed yet.**
- **ALL Track B tasks (B0–B7) now complete.** Next: enable Gemini (`.env`) and tune B5/B6 + run an eval; consider B2 only if prose-source recall needs it.

---

### 2026-08-20 — applied Track A's `fix.md` (3 of 4) + created `content.md`
- **Context:** Lakshay landed A3–A7 overnight and left `fix.md` — four issues, all in Track B files, so he (correctly, per §14.1) didn't touch them.
- **Fix #1 (demo-blocking, done):** `src/agent/router.py` `naming()` closure now falls back to `client.get_entity(cid)` (added by A5) and memoises into `name_of`. Previously edge destinations (e.g. the teams Alex is MEMBER_OF) printed as raw `ent_...` ids because only question-resolved entities were named — the LLM echoed the hash. Now "alex MEMBER_OF Eng-Oncall", not "alex MEMBER_OF ent_5eb3...". One `get_entity` per distinct unknown id, cached; no LLM calls.
- **Fix #2 (done):** `tests/test_agent.py` — `test_router_never_raises_against_unimplemented_client` was asserting yesterday's architecture (bare `GraphClient()` raised from every method). Since A3 landed, a bare client hits the live Layer 1 index and legitimately answers. Renamed to `test_router_never_raises_against_a_broken_client` with a `Broken(GraphClient)` subclass that raises from every method — guards the real property (router degrades to abstention, never crashes).
- **Fix #3 (done):** `src/llm/adapter.py` `_DEFAULT_MODELS["gemini"]` was the retired `gemini-2.0-flash`/`-lite` pair. Now `("gemini-3.5-flash", "gemini-flash-lite-latest")` — matching what A already set in `.env`. NB (from Lakshay): do NOT use `gemini-flash-latest`; it's a thinking model that returns empty at low `max_tokens` and silently disables the abstention gate.
- **Fix #4 (deferred, judgement call):** offline grader (`synthesize.grade`) waves everything through when `llm.available` is False, because Layer 1 now always returns something. Not urgent — with the Gemini key set this branch isn't reached and abstention is 8/8. Flagged for the AI-enhancement pass (add lexical-overlap guard, or treat "no LLM" as fail for info_not_found).
- **Tests:** 32 Track B tests green after the changes.
- **`content.md`:** created a beginner-facing, interview/demo explainer of the whole project (my parts vs Lakshay's, architecture, ER/conflict deep-dives, term cheat-sheet, Q&A). Standalone file, not the README (§14.2 — A owns README).
- **Open — owner review pending, then two big levers being evaluated:** (a) **full-scale graph rebuild** — Layer 2 still runs on the 180-doc fixture while Layer 1 covers 512K; this is the single biggest score+demo gain left (`just extract && resolve && conflicts && load`, watch Splink RAM on 8 GB). (b) **AI-retrieval enhancement — DONE, see below.**

### 2026-08-20 (cont.) — B5 critique / query-rewrite retry loop
- **What:** upgraded the grade-retry step from "search the same words with a bigger k" to a proper **critique loop**. `src/agent/synthesize.py::rewrite_query()` — when the first retrieval fails the grade, the LLM diagnoses what was missing and returns 1–3 rewritten queries (rephrase for paraphrased/semantic questions, or split multi-part/multi-hop questions into sub-queries). The router then re-retrieves (Layer 1 search + Layer 2 entity-facts) on each rewrite and merges. Costs **1 LLM call, only on questions that already failed** — targeted, small budget hit.
- **Graceful offline:** `rewrite_query` returns `[]` with no LLM, so the router falls back to the old broaden-the-search retry. All 32 Track B tests still green offline; ruff clean.
- **Refactor:** factored `_make_naming()` + `_entity_facts()` out of `_gather()` so the first pass and the rewrite retries share the same id→name cache and fact-pulling logic. `_gather(question, route, client, trace)` signature preserved (Lakshay's `fix.md` verify snippet still works).
- **Decided against LLM rerank** (owner + me): it spends tokens on *every* question for a marginal Document-Recall gain once the grader + rewrite loop already catch the misses. Not worth the free-tier budget.
- **Trace note:** `AnswerTrace` is `slots=True` (frozen, Track A) so there's no `rewrites` field — the rewrites are recorded inside `grade_reason` instead (visible in the UI trace).
- **Not committed yet.** Next after owner review: scale up the graph (8 GB RAM — go gently), then demo-question selection + benchmarking (discussion below with owner).

### 2026-08-20 (cont.) — graph scale-up on Shaurya's 8 GB Windows box (find the RAM ceiling)
- **Context:** this machine has NONE of Track A's infra (no HydraDB, no 512K corpus, no Layer 1 index — all on Lakshay's Mac). For graph scaling we don't need them: `extract→resolve→conflicts` produces `edges.parquet`, which Lakshay loads into HydraDB on the Mac. Validated the pipeline scales + found the RAM ceiling here, without touching the demo machine.
- **Setup:** downloaded 1 slice/source (`fetch --slices 1` → 45,000 docs), normalized (all 9 sources, fast). Dev scripts in scratchpad: `sample_docs.py` (samples N docs proportional to the real corpus mix), `run_stage.py` (in-process peak-RAM+time), `guarded_run.py` (kills a run if system free RAM < floor, so Splink can't swap-freeze the machine). Outputs isolated in `data/scale/<N>/` so the fixture-based `data/{resolved,graph}` the tests read stay intact.
- **Results (resolve = Splink = the only stage that grows):**

  | Docs | person mentions | resolve time | resolve peak RAM | entities | edges | superseded | contested |
  |---|---|---|---|---|---|---|---|
  | 500 | 2,222 | 4.1s | 0.33 GB | 1,910 | 2,887 | 135 | 0 |
  | 2,000 | ~8,900 | 20.9s | 1.30 GB | 5,897 | 9,761 | 753 | 2 |
  | 5,000 | 21,857 | (aborted) | **~3.5–4 GB** | — | — | — | — |

- **Ceiling found:** 5,000-doc resolve needs ~3.5–4 GB and **aborted twice** (guard fired at <350–400 MB free) because this machine only had ~2.4–3.8 GB free under normal app load. Clean aborts — no corruption, RAM released fully each time. Extract/conflicts stay trivial (<0.25 GB) at every scale.
- **Key diagnostic:** Splink's *core* is cheap (blocking 0.46s, predict 0.79s even at 5K). The memory blowup is in the **post-Splink clustering/entity-building** in `src/resolve/run.py` (materialising the full pairs frame + union-find + entity build in memory at once). So the 8 GB ceiling is an *our-code* memory-shape issue, not a Splink limit — a future optimisation (threshold-filter pairs before materialising, or stream the clustering) would push the ceiling well past 5K. Not doing it now.
- **Quality scales well:** ER merges look right and grow (`Alex Chen` 356 mentions, `Sam Lee` 294, `Maria G` 271 at 2K — the Sam/@soham collapse working at scale); conflicts richer (753 superseded, first 2 *contested* at 2K).
- **Recommendation:** on THIS machine, **2,000 docs is a comfortable safe ceiling** (already 11× the 180-doc demo graph). To go higher here, free ~2 GB (close browser/other VS Code) then retry — guarded runs make it safe to try. The full-corpus graph is Lakshay's Mac's job regardless.
- **Not committed yet.**

### 2026-08-20 (cont.) — CRITICAL ER FIX: entity-resolution over-merge at scale (found by evaluating the 2K graph)
This is the most important quality fix so far. Reference-level detail below because it changes the ER centrepiece and affects Lakshay's full-corpus graph too (same `src/resolve` code).

**How it was found.** Evaluating the 2,000-doc graph, the top "people" by alias count were nonsense: `Alex Chen` had **73 aliases** spanning `Alex Jenkins`, `Alex Torres`, `Alex Martinez`, even `Aisha Patel` + `aisha_rahman@…`. 22 such blob-entities absorbed **29% of all person-mentions**. Invisible at 180 docs (almost nobody shared a first name there); only shows once many people collide.

**Root-cause mechanism (important, non-obvious).** Splink emits a pairwise match probability per candidate pair; we merged every pair ≥ AUTO_MERGE_THRESHOLD via a plain union-find. Two failure modes compounded:
  1. **Transitive closure amplifies false positives.** Union-find takes the transitive closure, so even a handful of spurious high-confidence pairs chain a whole component together (A~B, B~C ⇒ A,B,C one entity).
  2. **Bare first names are bridges.** A mention with surface form just `Alex` (Slack speaker / @mention, no surname) can legitimately pair with `Alex Chen` AND `Alex Jenkins`; via that bare node the two distinct people join. Same for handles.
  (The stored `match_probability = 1.0` in clusters.parquet is OUR placeholder in `resolve_people`, NOT Splink's real score — don't read it as Splink certainty.)

**The fix — a surname-consistency guard in the union-find (`src/resolve/splink_er.py`).**
  - `_UnionFind` now tracks, per component root, the set of distinct surnames it contains. `union(a,b)` is **rejected** if merging would put two different surnames in one component. Mentions with no surname (bare `Alex`, a handle, an email-only) still attach freely, but once a component commits to a surname, a conflicting surname can never join — so the bridge chains are structurally broken.
  - `resolve_people` builds `surname_of` from the frame, and processes Splink pairs **strongest-first** (`sort_values("match_probability", desc)`) so a cluster commits to the correct surname before weaker/ambiguous links attach. The name↔email/handle bridge routes through the same guarded `union` (rejections don't count).
  - A post-run verification counts multi-surname clusters and prints `surname-check: OK` (must be 0). This is prevention, which subsumes the "blob-splitting" idea (no blob can form in the first place).

**Result (2,000 docs, re-run):** surname guard **blocked 102,953** cross-surname merges; person clusters 2,354→**3,661**; **max aliases-per-person 73 → 3**; **blob entities (≥11 aliases) 22 → 0**; `surname-check: OK`. Top entities by mention are now all real people (`Sam` 244, `Maria` 231, `Alex` 172, `Karthik Iyer`, `Ben Carter`, `Marissa Cole`, `Laura Bennett`…). Legit merges preserved (case variants, `Karthik Iyer`↔`karthik_iyer@redwood.ai`). Conflicts: 513 superseded / **5 contested** (fewer false conflicts from false merges; more genuine contested).

**Secondary fix — gmail `\nTo:` leak (`src/extract/sources.py`).** Threaded-email recipient values arrived as `\nTo: Ben Carter` (literal `\n` escape + leaked header label). Added `_clean_header_name()` (strips `\n`/`\r`/`\t` escapes and a leading From/To/Cc/Bcc label) applied to every display name in `GmailExtractor.people_from_header`. 91 leaked mentions → 0; entities with `To:` in the name 22→0.

**Residual limitations (known, acceptable, documented for honesty):**
  - **Bare-first-name collapse.** All bare `Sam` mentions with no surname/email merge into one `Sam` entity (244 mentions). If several real people are only ever called "Sam", they can't be split without more signal — the genuinely-ambiguous case the brief itself acknowledges. Far better than the old cross-surname blob.
  - **Junk-person tokens.** A handful of low-mention non-person tokens are still mis-typed as `person` (`Request`, `ERROR`, `KVCACHE`, `X-Request-Id`). Low impact (each 3–12 mentions; they never reach the high-traffic answer set). A follow-up could extend the person classifier stopwords in `src/extract/classify.py`. Not done yet.

**Also:** cleaned 4 pre-existing ruff nits in these two files (B007 unused loop var, B905 `zip` strict, I001 import sort) since they're Track B files and this diff touches them.

**Files changed:** `src/resolve/splink_er.py` (guard + verification), `src/extract/sources.py` (`_clean_header_name`). 34 Track B tests green, ruff clean. Corrected 2,000 graph regenerated in `data/scale/2000/` (gitignored). **Handover to Lakshay is the code (committed), not the data — he re-runs the pipeline on the full 512K corpus and gets the same fix.**
- **Not committed yet.**

### 2026-08-20 (cont.) — demo questions (all 4 types) from the corrected 2K graph → `demo/questions.md`
- **Deliverable:** `demo/questions.md` — one question per required type, each with the **verified** correct answer, source `dsid_` doc ids, expected route, and what it demonstrates. Mined from the corrected 2,000-doc graph; answers checked against graph facts + raw doc text (not guessed).
  1. **Lookup** — "What problem did Tess from Support report about the admin console?" → "Credit applied" toast, no ledger row, duplicate on retry (doc `dsid_d1e46e678eb…`).
  2. **Multi-hop** — "How is Ben Carter connected to Alex Rivera?" → via an email Ben sent Alex (`Ben —SENT→ doc ←RECEIVED— Alex`, doc `dsid_94f4e7d5b…`). Two-hop through a shared message; HydraDB `algo.MSpaths` (direction both) traverses it.
  3. **Conflict (marquee)** — "Which company does Alex Rivera work for now?" → **Echohealth now; was Helixpay & Starlitebank (superseded, valid_to 2027-04-19)**. One resolved person, 3 email aliases, 3 employer facts, current vs superseded with dates + provenance (docs `…echohealth`, `…helixpay`, `…starlitebank`). Graph-verified via `facts_about(WORKS_FOR)`.
  4. **Not-found** — "What is Redwood Inference's total annual revenue?" → abstain (verified 0 docs mention annual revenue; alternates "how many employees"/"office address" also 0).
- **Validation method / caveat:** components verified independently (`find_entity`, `facts_about`, `search` all return correct data). **Could NOT run full live router validation:** the **Gemini free-tier quota is exhausted for today** (`429 RESOURCE_EXHAUSTED`) — the router correctly *abstained* on everything rather than crashing (proves the graceful-degradation path again). Live synthesis was demonstrated working earlier this session (Alex/gibberish). The real demo runs on Lakshay's Mac (real Layer 1 index + full HydraDB graph + presumably fresh quota); re-confirm exact answers there before recording.
- **Two things surfaced worth noting:** (a) the router's `facts_about` is **outgoing-edges only**, so reverse-direction questions like "who is on the Support team?" (team is the edge *destination*) don't route well — a possible future improvement. (b) Multi-hop person↔person needs HydraDB's bidirectional path procedures; the local test-double's simple BFS doesn't traverse `person→doc←person`. Both are fine for the demo (HydraDB handles it); documented so they're not rediscovered.
- **`.env` note:** worth configuring `LLM_FALLBACK_PROVIDER`/`LLM_FALLBACK_API_KEY` (e.g. Groq) so a quota exhaustion mid-demo/eval fails over instead of abstaining. Adapter already supports the OpenAI-compatible path.
- **New file:** `demo/questions.md`. **Not committed yet.**

### 2026-08-20 (cont.) — LIVE run with the real Gemini key found+fixed a silent abstention-gate failure
- **Context:** owner added the Gemini key to `.env`. Ran the router live against `LocalGraphClient` (fixture graph, 180 docs) — the first real end-to-end test with an LLM.
- **BUG FOUND (serious):** pure-gibberish question was answered confidently instead of abstaining, and the trace read `"grader unavailable, proceeding"`. Diagnosed: **`gemini-3.5-flash` is a thinking model.** At `grade()`'s `max_tokens=120`, hidden reasoning consumes the whole budget → visible text comes back EMPTY (or truncated to `"SUPPORT"`). `grade()` treats empty as "proceed", so the abstention gate silently passed everything — would have cost all 20 info_not_found questions + Correctness everywhere. This is exactly `fix.md` #3/#4's trap, but biting *with* a key, not just without one. (Lakshay measured 3.5-flash as 3/3 reliable earlier; it is not, at low max_tokens — sample size was too small.)
- **Measured proof:** `max_output_tokens=120, default thinking → "SUPPORT"` (7 chars, truncated); `max_output_tokens=120, thinking_budget=0 → "SUPPORTED\n\n..."` (full, 3/3).
- **FIX (`src/llm/adapter.py`):** disable thinking (`ThinkingConfig(thinking_budget=0)`) for Gemini. BUT non-thinking models (`gemini-flash-lite-latest`, our cheap tier) **400 INVALID_ARGUMENT** on that flag — so the adapter tries with thinking disabled, catches the 400, learns the model into a module-level `_GEMINI_NO_THINKING` set, and retries without it (one-time discovery per model per process). Also disabled AFC to silence a noisy per-call SDK warning.
- **After the fix, live behaviour is correct:** Alex conflict → "currently on **Eng-Oncall**, replaced **Support**, superseded after 2026-03-15" (names via fix #1 + real dates); gibberish → **abstains**; rewrite loop fires on the failing questions (`retries: 1`).
- **Test hermeticity:** with `.env` present, `pytest` picked up the key and two agent tests started hitting the live model (non-deterministic wording + quota). Added `tests/conftest`-style autouse fixture *inside* `tests/test_agent.py` to unset `LLM_API_KEY`, keeping those tests on the deterministic offline path. Added **`tests/test_llm.py`** — hermetic (fake Gemini client, no key/network) regression tests that guard the thinking-disable + fallback logic so this bug can't silently return.
- Added `get_entity()` to `tests/support/local_client.py` (mirrors A5's real method) so fix #1's name resolution shows through in local checks too.
- **34 Track B tests green, ruff clean.** Files touched: `src/llm/adapter.py`, `tests/test_agent.py`, `tests/test_llm.py` (new), `tests/support/local_client.py`.
- **Not committed yet.**

---

## Issues

| # | Issue | Status | Cause / fix |
|---|---|---|---|
| — | _none yet_ | | |

---

## LLM budget tracker

We are on **free tiers** (`CLAUDE.md` §7.2). Target: under ~2,000 core calls total. Every LLM stage must be disk-cached by ID and resumable, so a rate-limit stop costs only time.

| Stage | Budgeted | Used so far |
|---|---|---|
| Entity merge adjudication (B3) | 200–500 | 0 |
| Conflict adjudication (B4) | 100–300 | 0 |
| Answer synthesis (B6) | ~800 | 0 |
| Prose extraction (B2, optional) | capped | 0 |

⚠️ The EnterpriseRAG-Bench scorer is itself an LLM judge and burns its own quota on every full 500-question eval run. Track A runs it, but budget for it jointly — don't discover exhausted quota on Aug 20.
