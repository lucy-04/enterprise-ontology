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
| B0 ontology v1 | 🔲 not started | **tonight — blocks A4** |
| B1 rule-based extractors | 🔲 not started | primary extraction path, no LLM |
| B2 LLM extraction (prose sources) | 🔲 not started | optional, budget-capped, skippable |
| B3 Splink entity resolution | 🔲 not started | **the centerpiece of the submission** |
| B4 conflict + bi-temporal model | 🔲 not started | |
| B5 router + abstention gate | 🔲 not started | |
| B6 answer synthesis | 🔲 not started | |
| B7 HERB spot-check | 🔲 not started | lowest priority, cut first |

Legend: 🔲 not started · 🔨 in progress · ✅ done · ⚠️ done but shaky · ❌ blocked

---

## Session log

_No entries yet. Append one per working session: goal → done → issues hit → where the next session picks up._

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
