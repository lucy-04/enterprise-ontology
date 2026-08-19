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
