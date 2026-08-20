# Demo questions — all four question types

Curated questions for the demo/video, one per required type from the brief
(simple lookup · multi-hop reasoning · conflict resolution · not-in-the-data).

**How these were chosen and verified.** They were mined from the **corrected
2,000-document graph** (`data/scale/2000/`, after the entity-resolution
over-merge fix) and each **answer + source document was verified directly against
the graph facts and the raw document text** — not guessed. The specific
documents (`dsid_…` ids come from filenames, so they are stable/global) are drawn
from the same corpus Track A loads in full, so these questions carry over to the
full-scale graph on the demo machine.

> **Note for the live demo (Lakshay's Mac).** The demo runs against the real
> Layer 1 index (511K docs, FTS5 + vectors) and the full HydraDB graph, where
> these same entities exist at richer scale. Re-confirm the exact answers there
> before recording (e.g. the "current" employer in the conflict question could
> gain more history in the full corpus). The router's live LLM synthesis was
> demonstrated working earlier; if a free-tier quota is exhausted, wait for reset
> or use the fallback provider.

---

## 1 · Simple lookup  →  route: `lookup`

**Q: "What problem did Tess from Support report about the admin console?"**

**Correct answer:** Tess (Support) reported that the admin console showed a green
*"Credit applied"* toast, but the back-office ledger had **no matching row**.
Re-applying the credit made the UI show a **duplicate toast**. It concerns
customer id 501023 and a trial credit of 1000.

**Source document(s):**
- `dsid_d1e46e678eb54add82bfcfd5d15abe6f` (slack / support channel)

**What it demonstrates:** a direct fact lookup — the answer sits in a single
document, retrieved by Layer 1 search and summarised. The baseline capability.

---

## 2 · Multi-hop reasoning  →  route: `multihop`

**Q: "How is Ben Carter connected to Alex Rivera?"**

**Correct answer:** Ben Carter (Redwood) is connected to Alex Rivera through an
email: **Ben Carter emailed Alex Rivera** (about the emit-latency agreement /
streaming), so they are linked via a shared message, not by any single document
stating "Ben knows Alex."

**Source document(s):**
- `dsid_94f4e7d5b8e04d01b115c55a63a9ea1d` (gmail — `From: Ben Carter … To: Alex Rivera`)

**What it demonstrates:** the answer requires **traversing the graph** —
`Ben Carter —SENT→ (email document) ←RECEIVED— Alex Rivera` — a two-hop
connection that plain keyword search cannot make. This is exactly what HydraDB's
native path procedures (`algo.MSpaths`/`SSpaths`, direction = both) return in one
call, which is the "Best Use of HydraDB" story.

*(The simplified test-double client used for local dev doesn't traverse this;
HydraDB's path procedures on the demo machine do.)*

---

## 3 · Conflict resolution  →  route: `conflict`   ⭐ the marquee question

**Q: "Which company does Alex Rivera work for now?"**

**Correct answer:** Alex Rivera **currently works for Echohealth**
(`alex.rivera@echohealth.com`). Earlier he was at **Helixpay** and **Starlitebank**
— those are **superseded** (valid until 2027-04-19), not deleted. The system
surfaces the current answer *and* the prior ones, each with its date and source,
instead of silently picking one.

**Source document(s):**
- `dsid_f7a802c881614dcc9a01c2f373fafd3d` (gmail — **current**, `alex.rivera@echohealth.com`)
- `dsid_94f4e7d5b8e04d01b115c55a63a9ea1d` (gmail — superseded, `alex.rivera@helixpay.com`)
- `dsid_bc4f13b208fc47268afbb2b2e95354dc` (gmail — superseded, `alex.rivera@starlitebank.com`)

**What it demonstrates:** the bi-temporal conflict model. The same resolved
person (`Alex Rivera`, one node with all three email aliases) has three
contradictory employer facts; the graph keeps all of them, marks the latest
`current` and the rest `superseded` with `valid_to` dates and provenance. This is
the brief's "which of two contradictory statements to trust," shown honestly.

---

## 4 · Not in the data  →  system **abstains** (no route answers)

**Q: "What is Redwood Inference's total annual revenue?"**

**Correct answer:** *The system should abstain* — e.g. "I don't have enough
information to answer that." This figure is **not present** in the corpus
(verified: 0 documents mention annual revenue; customer-level "ARR" appears but is
not company revenue).

**Source document(s):** none (that is the point).

**What it demonstrates:** the grade-before-answering / abstention gate. Rather
than hallucinating a plausible number, the system recognises the absence of
supporting evidence and declines. These "info-not-found" questions are free
points most systems lose by making something up.

*Alternate absent questions (also 0 documents): "How many employees does Redwood
Inference have?", "What is Redwood's office address?"*

---

## Quick reference

| # | Type | Question | Route | Answer in one line |
|---|---|---|---|---|
| 1 | Simple lookup | Tess's admin-console problem | `lookup` | "Credit applied" toast with no ledger row; duplicate on retry |
| 2 | Multi-hop | Ben Carter ↔ Alex Rivera | `multihop` | Connected via an email Ben sent Alex |
| 3 | Conflict | Alex Rivera's current employer | `conflict` | Echohealth now; was Helixpay/Starlitebank (superseded) |
| 4 | Not found | Redwood's annual revenue | *(abstain)* | Not in the data — system declines |
