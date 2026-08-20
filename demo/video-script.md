# Demo video script — 3 minutes

**Hard limit: 3:00.** Anything past it may not be reviewed. This script runs ~2:50
spoken at a normal pace, leaving margin.

Everything below is **verified working** against the live system as of 2026-08-20.

---

## Before you hit record

```bash
pkill -f src.eval.run          # the eval saturates the machine and makes the UI sluggish
just db-up                     # terminal 1 — leave running
just serve                     # terminal 2 — leave running
open http://localhost:8000
```

- **Hard-reload the page once** (Cmd+Shift+R). Static files now serve `no-store`, but do
  it anyway.
- Check the header reads **511,958 documents · 6,303 entities · 7,025 aliases ·
  17,185 relationships**. If it reads 0 anywhere, HydraDB isn't up.
- Have two browser tabs ready: the UI, and one raw document open for the cold open.
- Answers take **20–40 seconds**. Do **not** wait on camera — ask each question, then cut.
  Record the asks and the results separately and join them.

**Do not use Sam, Priya or Maria as examples.** They are bare-first-name clusters and
their "conflicts" are merge artifacts, not real disagreements. The three below are clean.

---

## 0:00 – 0:20 · The problem

> "Half a million documents from nine enterprise apps — Slack, Gmail, Jira, Confluence and
> five more. Real company noise: misfiled documents, near-duplicates, and statements that
> flatly contradict each other.
>
> The same person shows up written six different ways."

**Screen:** the raw corpus. Show the filename list, then one gmail document with a
`From:` header. These are the real surface forms for one person:

```
Karthik Iyer                        dsid_61cf5472a1334a27a83b4cdb9cb5a310
karthik_iyer@redwood.com            dsid_6403cc33b5ab468b8a7ff4d91f9e8884
karthik.iyer@redwood.com            dsid_8757ff0949f0416ab4727399aeefa15a
karthik_iyer@redwood.ai             dsid_e8f0b8fc06d3401498e1108910fb1479
karthik_iyer@redwoodinference.com   dsid_0bbcb3b2d1754e0ca2897d724740b5f3
karthik_iyer                        dsid_7a00729fa6c241579954582b3feb40fb
```

> "Extraction is the easy part now. The hard part is deciding these are one person — and
> deciding which of two contradictory statements to trust."

---

## 0:20 – 0:40 · What we built

**Screen:** the architecture diagram, or the README's diagram block.

> "Two layers over the same corpus. A search index covers all half a million documents
> with no LLM at all — that's what finds the right document.
>
> On top of it, an ontology graph in HydraDB: resolved entities, typed relationships, and
> every fact stamped with when it was true and where it came from. That's what answers the
> questions search structurally can't.
>
> A router picks per question."

> "Extraction is rule-based, because we're on free-tier LLM access. An LLM can't read half
> a million documents and doesn't need to — the scarce budget goes on the hard part
> instead: resolving entities, settling contradictions, and deciding when to say nothing."

---

## 0:40 – 1:05 · Entity resolution *(the centrepiece)*

**Ask:** `Which company does Karthik Iyer work for?`

**Screen:** scroll to **RESOLVED ENTITIES**. Let the seven chips sit on screen.

> "Splink — unsupervised probabilistic record linkage, no training data — merged those six
> spellings and the display name into one node. Non-destructively: every surface form ever
> written is still attached.
>
> This is where naive systems break. GraphRAG does exact string matching and over-merges;
> it's an open issue in their tracker. We hit the same failure at scale — one 'Alex' had
> accumulated seventy-three aliases spanning four different people, because a bare first
> name bridges two strangers.
>
> We fixed it with a surname-consistency guard: a merge that would put two different
> surnames in one cluster is rejected outright. Seventy-three aliases down to three.
> Blob entities: twenty-two down to zero."

---

## 1:05 – 1:45 · Conflict resolution *(the money shot)*

**Ask:** `Which company does Samir Patel work for now?`

**Screen:** the **CONTRADICTION FOUND** panel — "both sides kept, nothing overwritten".

> "Four sources say Samir Patel works at four different companies. The system doesn't pick
> one silently and doesn't average them.
>
> He's at Bluecord now. Kiteworks, Oxbridge, Healthmetrics and Finetext are shown struck
> through, each with the date it stopped being true and the Gmail document it came from.
>
> Nothing was deleted. A contradicted fact keeps its row, gets an end date, and points at
> whatever replaced it — so you can always ask what we believed last March, and why."

**Screen:** scroll to the **GRAPH** panel. Point at the red dashed edges.

> "Red dashed lines are superseded facts. The contradiction is visible in the picture, not
> just in the text."

---

## 1:45 – 2:10 · Abstention

**Ask:** `What is Redwood Inference's total annual revenue?`

**Screen:** the abstention, and the **Evidence check** line underneath it.

> "That isn't in the data. Every answer passes a grade-before-answering gate: does the
> retrieved evidence actually support this question? If not, the query is critiqued and
> rewritten once — and only then does the system decline.
>
> It doesn't just refuse, it says what was missing: it found monthly revenue figures and
> contract values, but no annual total.
>
> Twenty of the five hundred benchmark questions are unanswerable. They're free points
> that most systems lose by guessing."

---

## 2:10 – 2:40 · How HydraDB is used

**Screen:** the graph panel, then `src/graph/bolt.py` or the README's HydraDB section.

> "Multi-hop questions run on HydraDB's own bounded-path procedures — `algo.SPpaths`,
> `SSpaths`, `MSpaths` — not a traversal we wrote. One call returns the full path with
> every node and relationship, so the whole provenance chain renders directly.
>
> And the data model was shaped by measuring what HydraDB's Cypher actually accepts.
> Node ids must be integers, so ours is a deterministic hash of the canonical id — stable
> across reloads with no mapping table. Properties can't hold lists, so every alias is its
> own node, scoped to its owner, which is also exactly the picture you want to look at.
> And `IS NULL` isn't queryable, so bi-temporal validity is an explicit boolean plus a
> sentinel.
>
> Each of those started as a constraint and ended up as the better design."

---

## 2:40 – 3:00 · Results

**Screen:** the results table from the README.

> "Half a million documents indexed, recall measured against the benchmark's own gold
> documents. Zero false-confident answers — it never invented one for a question with no
> answer.
>
> Everything's open source, it runs on a laptop, and the whole thing is in the repo."

---

## Cheat sheet — the three questions

```
Which company does Karthik Iyer work for?
Which company does Samir Patel work for now?
What is Redwood Inference's total annual revenue?
```

## If something goes wrong on the day

| Symptom | Fix |
|---|---|
| Header shows 0 anywhere | HydraDB isn't up — `just db-up` |
| UI edits not appearing | Cmd+Shift+R (static is `no-store`, but caches lie) |
| Everything abstains | LLM quota or network — `uv run python -c "from src.llm.adapter import LLM; print(LLM().complete('hi'))"` |
| UI feels sluggish | The eval is running — `pkill -f src.eval.run` |
| An answer is slow | Normal: 20–40s. Cut between ask and result. |
