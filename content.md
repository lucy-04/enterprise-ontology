# The Project, Explained Simply

*A complete, plain-English guide to the whole project — for interviews, demos and exhibitions.
Every technical term is explained the first time it appears. If you read this top to bottom,
you'll be able to explain the project confidently to anyone.*

> **How to use this document**
> - **Section 1–3** = the story: what problem, why it's hard, what we built. Read these first.
> - **Section 4–8** = how it actually works, stage by stage.
> - **Section 9** = exactly which parts were **yours (Shaurya, Track B)** vs your teammate's.
> - **Section 10–12** = the impressive bits to show off, the results, and how to demo.
> - **Section 13** = a cheat-sheet of every technical word.
> - **Section 14** = likely interview questions with ready answers.

---

## 1. The one-sentence pitch

> We take **half a million messy company documents** from nine different apps (Slack, Gmail, Jira, etc.) and turn them into a **clean, connected map of who's who and what's what**, so you can ask plain-English questions and get trustworthy answers — including "these two documents disagree, here's which is current" and "that information isn't in here" (instead of making something up).

If someone only gives you 15 seconds, that's the whole thing.

---

## 2. The original problem (the hackathon brief)

This project was built for **Hack Hydra**, a hackathon run by a company called HydraDB. Our track was **"Enterprise context and ontology."** Here is the brief, in plain terms:

> You get about **500,000 documents** pulled from a fake-but-realistic company ("Redwood Inference"), across **nine sources**: Slack, Gmail, Linear, Google Drive, HubSpot, Fireflies, GitHub, Jira and Confluence. They're as messy as a real company's: **misfiled documents, near-duplicates, and statements that flatly contradict each other.**
>
> Your job: turn that mess into a **clean, queryable ontology** and then **answer questions** — from simple lookups, to questions that need connecting several documents, to resolving contradictions, and correctly saying **"the answer isn't in here"** when it genuinely isn't.
>
> The brief's own hint: *"Extraction is the easy part now that LLMs are cheap. The hard part is entity resolution and ontology alignment — deciding that 'Sam', '@soham' and 'S. Ratnaparkhi' are one person, and figuring out which of two contradictory statements to trust."*

**Let's define the two key words in that brief:**

- **Ontology** — a fixed "vocabulary" for a body of knowledge: the complete list of the *kinds of things* that exist (people, tickets, meetings…) and the *kinds of relationships* between them (works-for, owns, mentions…). Think of it as the **legend on a map** — it tells you what every symbol means before you read the map.
- **Entity resolution** — deciding that several different-looking names refer to the **same real person or thing**. "Sam", "@soham" and "S. Ratnaparkhi" are one human being; a computer has to figure that out. This is the heart of the whole project.

### The four kinds of questions we must answer

The project is graded on answering 500 real questions that fall into these buckets:

| Question type | Example | Why it's hard |
|---|---|---|
| **Simple lookup** | "What does the onboarding playbook cover?" | Easy-ish — the answer sits in one document. |
| **Multi-hop reasoning** | "Which team does the person who filed ticket SUP-359481 work on?" | No single document has the whole answer — you must **connect** several. |
| **Conflict resolution** | "Which team is Alex on now?" (two sources disagree) | You must show **both sides** and say which is current, not silently pick one. |
| **"Not in the data"** | "What's our policy on X?" — when there is no such policy | The system must **admit it doesn't know** instead of inventing an answer. |

("Multi-hop" just means the answer requires *hopping* across two or more connected pieces of information.)

---

## 3. Our big idea: two layers over the same documents

Here's the core insight. Two *different* kinds of questions need two *different* tools:

- **"Which documents are relevant?"** → this is a **search** problem.
- **"How do these facts connect, and which do I trust?"** → this is a **reasoning** problem that search can't do.

So we built **two layers** over the same 500K documents, and a **router** that picks the right one for each question.

```
                         ┌─────────────────────────────────────────────┐
   500,000 documents ──▶ │ LAYER 1: SEARCH INDEX (all documents)       │  ← "which docs are relevant?"
                         │  keyword search + meaning-based search       │
                         └─────────────────────────────────────────────┘
                         ┌─────────────────────────────────────────────┐
                     ──▶ │ LAYER 2: KNOWLEDGE GRAPH (HydraDB)          │  ← "how do facts connect / which to trust?"
                         │  people, tickets, meetings + relationships   │
                         └─────────────────────────────────────────────┘

   your question ──▶ ROUTER ──▶ picks Layer 1, Layer 2, or both ──▶ answer (with the documents that prove it)
```

**Define the pieces:**

- **Search index** — like the index at the back of a textbook, but automatic: given a query, it instantly returns the most relevant documents. Ours combines two styles:
  - **Keyword search** — matches the actual words ("KMS", "latency"). Fast and literal.
  - **Vector / semantic search** — matches by *meaning*, not exact words, so "sign-in problems" can find a doc about "authentication failures." It does this using **embeddings**.
  - **Embedding** — a way of turning a piece of text into a list of numbers (a "vector") such that texts with similar *meaning* get similar numbers. Comparing the numbers = comparing the meaning.
- **Knowledge graph** — a network of **nodes** (things) and **edges** (relationships). Example: a `Person` node "Alex" connected by a `MEMBER_OF` edge to a `Team` node "Eng-Oncall." Graphs are great at "connect the dots" questions because the dots are literally connected.
  - **Node** = a thing (a person, a ticket, a meeting).
  - **Edge** = a labelled link between two things (Alex —MEMBER_OF→ Eng-Oncall).
- **Router** — a small piece of logic that reads the question and decides which layer(s) to use.

**Why two layers?** The graded score measures two separate things — *did you find the right documents* and *did you reason correctly*. Neither layer alone can do both. Together they cover the full range of questions.

### One more crucial design choice: we barely use the AI model

- **LLM (Large Language Model)** — an AI like ChatGPT/Gemini that reads and writes text.
- We're on **free AI access only** (~a few thousand calls a day). An LLM **cannot** read 500,000 documents — far too expensive.
- **So we don't ask it to.** Most company documents are structured enough to read with **rules** (fixed patterns), for free, at full scale. We call this **rule-based extraction**.
- **Extraction** = pulling structured facts (people, tickets, relationships) out of raw text.
- We save the scarce AI budget for the genuinely hard bits: judging the *ambiguous* entity-merges, settling *unclear* contradictions, and *writing* the final answers. This matches the brief's own hint that "extraction is the easy part."

---

## 4. The full pipeline, stage by stage

Here's the entire journey from raw files to answers. Each stage hands its output to the next as files on disk, so the two teammates could work independently.

```
 raw .txt files
      │  (1) NORMALIZE   – clean every source into one common shape
      ▼
 normalized documents
      │  (2) EXTRACT     – rules pull out people, tickets, relationships  [MINE]
      ▼
 candidate "mentions" + "relations"
      │  (3) RESOLVE     – merge duplicates into one identity (Splink)     [MINE]
      ▼
 canonical entities (one node per real person/thing)
      │  (4) CONFLICTS   – detect contradictions, keep both sides dated    [MINE]
      ▼
 final graph edges
      │  (5) LOAD        – write nodes + edges into HydraDB
      ▼
 the knowledge graph
      │  (6) ASK         – router + grade + answer                         [MINE]
      ▼
 answer + the documents that prove it
```

Let me define the new words, then explain each stage.

- **Normalize** — reformat many different layouts into **one common shape** so later stages don't care whether a document came from Slack or Gmail.
- **Mention** — a single appearance of something in one document. If "Alex" is written in three documents, that's three *mentions* (which may or may not be the same real Alex — that's for the next stage to decide).
- **Relation** — a claimed relationship found in one document (e.g., "Alex is on the Support team").
- **Canonical entity** — the single, official record for a real-world thing after all its duplicate mentions are merged. "Canonical" just means "the one true/official version."
- **Splink** — a free, well-known software library that does entity resolution using probability (explained in Section 6).
- **HydraDB** — the graph database this hackathon is about (a **database** is just organized storage; a **graph database** stores nodes and edges).

### Stage 1 — Normalize *(teammate's work — Track A)*
Every source writes documents differently: Gmail has `From:`/`To:` headers, Slack has `speaker: message` lines, Jira is prose. This stage parses each into one common shape with fields like `title`, `body`, `author_refs` (raw names found), `timestamp`, etc. No AI. It also pulls out useful bits like email headers and cross-reference IDs.

### Stage 2 — Extract *(MY work — Track B)*
Rules read each normalized document and emit **mentions** (this is a Person / Ticket / Meeting…) and **relations** (this Person SENT this document). For example, from a Gmail header `From: Karthik Iyer <karthik@redwood.com>` we emit a Person mention "Karthik Iyer", and a `SENT` relation to that email. From a Jira ticket we pull the ticket ID and every other ticket/PR it references. **All done with pattern-matching, no AI, so it runs over all 500K documents for free.**

### Stage 3 — Resolve *(MY work — Track B)*
Merge the mentions that refer to the same real thing. "Karthik Iyer" (from Slack) and "karthik@redwood.com" (from Gmail) become **one** Person node. This is entity resolution — the centerpiece. (Details in Section 6.)

### Stage 4 — Conflicts *(MY work — Track B)*
When two sources disagree ("Alex is on Support" vs "Alex is on Eng-Oncall"), we **don't delete either**. We keep both, mark the older one as "was true until [date], now replaced," and remember which document said what. (Details in Section 7.)

### Stage 5 — Load *(teammate's work — Track A)*
Take the finished list of nodes and edges and write them into HydraDB so they can be queried.

### Stage 6 — Ask *(MY work — Track B)*
When a question comes in: classify it, gather evidence from the two layers, **check the evidence actually supports an answer**, then either answer (citing the exact documents) or **abstain** ("I don't have enough information"). (Details in Section 8.)

---

## 5. The ontology — the "legend on the map" *(MY work — Track B)*

Before extracting anything, I had to **fix the vocabulary** — the complete list of node types and edge types the graph is allowed to contain. This is written once, up front, in a file called `ontology.yaml`, and then **frozen** (never changed mid-run).

**Why fix it up front?** If every document could invent its own categories, the same idea would appear under fifty slightly different names and entity resolution would get *worse*. (A famous competing system, Microsoft's GraphRAG, has exactly this bug — we deliberately avoided it.)

Our ontology has **16 node types** and **22 edge types**. A few examples:

| Node types (things) | Edge types (relationships) |
|---|---|
| Person, Bot, Team, Role, Organization | SENT, RECEIVED, MENTIONS (communication) |
| Ticket, PullRequest, Meeting, Incident | MEMBER_OF, WORKS_FOR, HAS_ROLE (identity) |
| Channel, Document, Component, Product | REFERENCES, RELATES_TO, RESOLVES (work links) |
| Project, Topic, Alias | OWNS, ASSIGNED_TO, PART_OF, CONTESTED… |

Notice **Bot** is separate from **Person** — automated accounts like "deploy-bot" are real actors but not humans, so keeping them apart stops them polluting "who did X?" answers.

---

## 6. Entity resolution — the centerpiece *(MY work — Track B)*

This is the single most important and impressive part, and it was **entirely mine**.

**The problem again:** the same person appears under many surface forms — "Sam", "@soham", "S. Ratnaparkhi", "sam@redwood.com". We must decide these are one human and merge them into **one node** that remembers every name.

**How we do it — two paths:**

**Path A — exact matching for things with IDs (tickets, PRs, meetings).**
A ticket "ENG-30521" is the same ticket everywhere it's mentioned. So all mentions sharing that ID become one node — instantly, deterministically. This is huge, because it means a ticket mentioned in a Slack message and in a Jira document gets automatically connected — that's the "connect two documents" superpower, for free.

- **Deterministic** = always gives the same, exact answer (no guessing).

**Path B — probabilistic matching for people (using Splink).**
People don't have clean IDs, so we use **Splink**, which scores how likely two mentions are the same person based on how similar their names, emails and handles are.

- **Probabilistic** = based on likelihood/probability rather than exact certainty.
- Splink gives each pair a **match probability** (0 to 1). We use three bands:
  - **High (≥0.92)** → merge automatically.
  - **Low (<0.55)** → definitely different people.
  - **Middle** → genuinely unsure → this is where we spend a little AI budget to have the LLM decide.
- Splink is **unsupervised**, meaning it needs **no training examples** — it learns the patterns from the data itself. It can link ~1 million records in about a minute on a laptop.

**The clever extra — bridging names to emails.**
Splink compares *how names look*, so it can't tell that "Karthik Iyer" and "karthik_iyer@redwood.com" are the same (the letters don't resemble each other enough). I added a **bridge**: I generate the email-forms a name could plausibly produce (karthik.iyer, karthik_iyer, kiyer…) and merge when one exactly matches an email's front part. This is high-precision, and it's what makes the "one name in Slack = one email in Gmail = one person" demo actually work.

**Non-destructive merging.** When we merge, we **keep every original name** as an **alias** on the single node. Nothing is thrown away — the whole point of the demo is showing "look, all these names became one person, and here are all the names."

- **Alias** = an alternative name/handle/email for the same entity.

**Result on our test data:** 282 person-mentions collapsed into 194 real people, with verified cross-source merges like "Karthik Iyer" + his email, "Marcus Lin" spanning Fireflies + Gmail, etc.

---

## 7. Handling contradictions — the "bi-temporal" model *(MY work — Track B)*

Real companies contradict themselves. "Alex is on Support" (an old Slack message) vs "Alex is on Eng-Oncall" (a newer one). The brief demands we **surface** the disagreement, not silently pick one.

Our rule: **never delete a contradicted fact.** Instead we mark it. This is called a **bi-temporal** model.

- **Bi-temporal** — we track *time* on facts: when a fact was true, and when it stopped being true. So a fact isn't just "true/false" — it's "true **until** this date, then replaced."

**How the winner is chosen** when two facts clash:
1. **Source priority** — a formal system-of-record (Jira, Linear) is trusted over casual chat (Slack). We have a fixed priority table.
2. **Recency** — if priorities tie, the newer statement wins.
3. **Genuine tie** — if we truly can't decide, we mark it **contested** and the answer shows *both* sides.

The loser isn't erased — it gets a `valid_to` date ("was true until…") and a pointer to the winner (`superseded_by`).

- **Superseded** = replaced by a newer/better version.
- **Contested** = sources genuinely disagree and we're honestly flagging it.

**Real example our system found automatically:**
> "alex" was on team **Support** (until 2026-03-15), then moved to **Eng-Oncall** (current). Both are kept, so the system can answer *"Alex is on Eng-Oncall now, was on Support until March 2026"* — with the source document as proof.

**A technical detail worth knowing (it impressed the judges' "Best Use of HydraDB" criterion):** HydraDB's query language can't ask "where end-date is empty," so instead of leaving the end-date blank for current facts, we store an explicit **`is_current`** true/false flag. Small decision, but the whole conflict feature would break without it.

---

## 8. Answering questions — the "agent" *(MY work — Track B)*

The final piece takes a question and produces an answer. This is sometimes called the **agent** or **query router**. It follows a disciplined loop:

1. **Classify** the question — lookup / multi-hop / conflict / count. (Simple keyword rules, no AI.)
2. **Retrieve evidence** — search the documents (Layer 1) and pull connected facts from the graph (Layer 2).
3. **Grade before answering** — *this is the key trick.* Before writing anything, check: **does this evidence actually answer the question?**
4. **Retry once** if the grade fails (search wider).
5. **Answer or abstain** — if the evidence supports an answer, write it and **cite only the documents that actually contributed**. If not, say **"I don't have enough information."**

Two important terms:
- **Abstain / abstention** — deliberately choosing *not* to answer when the information isn't there. This is worth free points: 20 of the 500 questions are unanswerable, and most teams *lose* those points by **hallucinating**.
- **Hallucination** — when an AI confidently makes up a false answer. The grade-before-answering step is our defense against it.
- **Provenance / citation** — the record of *which documents* an answer came from. We only cite documents that genuinely contributed, because citing extra ones is penalized in scoring.

**Grade-before-answering** is borrowed from a prior project idea (nicknamed "Refract"): grade → retry → abstain. It protects correctness on *every* question, not just the unanswerable ones.

**The AI provider:** the answer-writing uses **Google Gemini** by default (with the option to swap in others like Groq). Everything is built to run **without** an AI key too (it falls back to simpler extracted answers), so the system never fully breaks — the AI just makes it better.

---

## 9. Who did what — my part vs my teammate's

This was a **two-person project** with a strict split so we never stepped on each other. The dividing line: **Track A moves and stores data; Track B decides what it means.**

### 🟢 MY WORK — Track B (the "AI / meaning" half — Shaurya)

| Piece | What it is | File(s) |
|---|---|---|
| **Ontology** | Defined the fixed vocabulary of the graph | `ontology/ontology.yaml` |
| **Extraction** | Rules that pull people/tickets/relationships from every source | `src/extract/` |
| **Entity resolution** | Splink + the name↔email bridge — the centerpiece | `src/resolve/` |
| **Conflict model** | Bi-temporal "keep both sides, dated" logic | `src/conflicts/` |
| **The agent** | Router, grade-before-answering, abstention, answer writing | `src/agent/` |
| **AI adapter** | One swappable interface to Gemini/others, with caching | `src/llm/` |
| **HERB test** | A separate proof that entity resolution works | `src/resolve/herb_check.py` |

**In interview terms:** *"I owned everything about turning messy text into meaning — the schema, extracting facts, deciding who's who, resolving contradictions, and the question-answering agent that decides when to answer and when to admit it doesn't know."*

### 🔵 NOT my work — Track A (the "infrastructure" half — Lakshay)

| Piece | What it is |
|---|---|
| Downloading the corpus + normalizing all 9 sources into one shape |
| Building the two search indexes (keyword + vector) |
| Building and loading the HydraDB graph; the query functions |
| The web API and the demo user-interface (the visual graph you click through) |
| The evaluation runner that scores us against the benchmark |

**How the two halves connect:** we agreed on a small set of **contracts** — exact file formats (using **Parquet** files) and one function (`answer()`) — up front. As long as both sides honored those, neither blocked the other.
- **Parquet** = an efficient file format for tables of data, used to pass results between the two halves.

---

## 10. What makes this project stand out (talking points)

These are the things to emphasize to judges/interviewers:

1. **Entity resolution done properly, with real evidence it works.** Not string-matching — actual probabilistic linkage (Splink) plus a name↔email bridge, *proven* on a separate dataset (HERB) at **87% team-recovery**.
2. **Contradictions are surfaced, not hidden.** The bi-temporal model answers "X now, was Y until [date]" with sources — most systems just overwrite and lose the history.
3. **It knows when to shut up.** The grade-before-answering gate makes it abstain on unanswerable questions instead of hallucinating — free points others lose.
4. **It scales for free.** Rule-based extraction reads all 500K documents with no AI cost; AI is spent only on the genuinely hard 2,000-or-so decisions. This was a *deliberate* design matching the brief's hint, not a shortcut.
5. **Graph-native reasoning.** Multi-hop questions use HydraDB's built-in path-finding, and cross-document links (a ticket cited in five places) happen automatically and deterministically.
6. **Honest engineering.** Everything is provenance-tracked (every fact remembers its source document), and the system degrades gracefully if a component (AI, database) isn't available.

---

## 11. The results / numbers to quote

*(Measured on our 180-document development sample and the HERB benchmark.)*

| Metric | Result |
|---|---|
| Node types / edge types in the ontology | 16 / 22 |
| Facts extracted from 180 sample docs | 1,011 entities + 772 relationships, **zero AI calls** |
| People resolved | 282 mentions → 194 real people (cross-source merges verified) |
| Contradictions | detected + dated automatically (e.g. the Alex team change) |
| **HERB entity-resolution test** | **87% of true team membership recovered from artifacts alone**, 71% precision, disambiguating 59% of ambiguous shared-name references |
| Automated tests | **54 passing** |

**About the HERB number specifically** (a strong, concrete claim): HERB is a benchmark with **530 employees who share only 98 names** — "Hannah Taylor" is 10 different people. Our system figures out *which* Hannah Taylor is meant, using context (who else appears alongside her), and recovers 87% of the true teams **without ever peeking at the answer key**.

---

## 12. How a demo / testing is performed

**Automated testing (proves the parts work):**
- We have **54 automated tests** covering extraction, resolution, conflicts, the agent, and HERB. Run with one command (`pytest`). Green = everything behaves.
- Each test pins a specific behavior — e.g. "prose labels like 'Steps to reproduce' must NOT be mistaken for people," or "two different people named Ben must NOT be merged."

**The live demo (what you'd show an audience) — a 3-minute story:**
1. **Show the mess.** Open 2–3 raw documents where the same person appears under different names in different apps.
2. **Show the resolution.** Show the single resolved Person node with all those names hanging off it as aliases — "the computer figured out these are one person."
3. **Ask three live questions:**
   - a **simple lookup** → correct answer + the source document,
   - a **conflict question** ("which team is Alex on now?") → the system shows *both* the current and the old answer, each with a date and source,
   - a **"not in the data" question** → the system correctly says *"I don't have enough information"* instead of inventing.
4. **End on the scoreboard** — the benchmark results table. Concrete numbers beat a code tour.

**The visual:** there's a web page (my teammate's part) that shows the answer, the documents behind it, the resolved person with all aliases, and a clickable node-link graph of the connected facts.

---

## 13. Cheat-sheet — every technical term in one place

| Term | Plain meaning |
|---|---|
| **Ontology** | The fixed "legend": the allowed kinds of things and relationships. |
| **Node / Edge** | A thing / a labelled link between two things, in a graph. |
| **Graph / Knowledge graph** | A network of connected facts (nodes + edges). |
| **Graph database (HydraDB)** | Storage built to hold and query a graph. |
| **Entity** | A real-world thing (a person, a ticket). |
| **Entity resolution** | Deciding several names mean the same entity, and merging them. |
| **Canonical** | The single official version (after merging). |
| **Alias** | An alternative name/handle/email for the same entity. |
| **Mention** | One appearance of something in one document. |
| **Relation** | A claimed relationship found in a document. |
| **Extraction** | Pulling structured facts out of raw text. |
| **Rule-based** | Using fixed patterns, not AI. |
| **Normalize** | Reformat many layouts into one common shape. |
| **LLM** | A large AI language model (ChatGPT/Gemini). |
| **Hallucination** | An AI confidently making up a false answer. |
| **Abstain** | Choosing not to answer when the info isn't there. |
| **RAG** | "Retrieval-Augmented Generation" — fetch relevant text, then let an AI answer using it. Our whole system is an advanced RAG. |
| **Search index** | An automatic back-of-book index over all documents. |
| **Keyword search** | Matching the exact words. |
| **Vector / semantic search** | Matching by meaning, using embeddings. |
| **Embedding** | Turning text into numbers so similar meanings get similar numbers. |
| **Splink** | Free library for probabilistic entity resolution. |
| **Probabilistic / Deterministic** | Based on likelihood / based on exact certainty. |
| **Unsupervised** | Learns from data with no training examples. |
| **Multi-hop** | An answer that needs connecting 2+ facts. |
| **Provenance / Citation** | The record of which documents an answer came from. |
| **Bi-temporal** | Tracking when a fact was true and when it stopped. |
| **Superseded** | Replaced by a newer/better version. |
| **Contested** | Sources genuinely disagree; both shown. |
| **Router / Agent** | The logic that picks a strategy and produces the answer. |
| **Parquet** | An efficient table-file format used to pass data between the two halves. |
| **Provider adapter** | One swappable interface to different AI providers. |

---

## 14. Likely interview questions (with ready answers)

**Q: What was the hardest part?**
> Entity resolution. Deciding "Sam", "@soham" and "sam@redwood.com" are one person. String-matching fails on that, so I used Splink (probabilistic linkage) plus a custom bridge that connects names to emails. I proved it works on a separate benchmark (HERB), recovering 87% of true team membership without looking at the answers.

**Q: Why not just feed everything to ChatGPT?**
> Two reasons. Cost — an AI can't read 500,000 documents on a free tier. And correctness — the hard part isn't reading text, it's deciding who's who and which of two contradictory facts to trust. So I read the documents with cheap rules and spent the tiny AI budget only on the genuinely ambiguous decisions and writing final answers.

**Q: How do you handle contradictions?**
> I never delete a contradicted fact. I keep both, mark the old one "true until [date], now replaced," and pick the winner by trusting formal systems (Jira) over chat (Slack), with recency as a tie-breaker. If it's a true tie, I flag it "contested" and show both sides. This is called a bi-temporal model.

**Q: How does it avoid making things up?**
> Before answering, it grades whether the retrieved evidence actually supports an answer. If not, it retries once, then abstains — "I don't have enough information." That wins the unanswerable questions outright and protects accuracy everywhere else.

**Q: What was your specific contribution?**
> I owned the entire "meaning" half: the ontology (schema), rule-based extraction, entity resolution with Splink, the contradiction/bi-temporal model, and the question-answering agent with its abstention gate. My teammate owned the infrastructure — data loading, search indexes, the HydraDB graph storage, the API and the UI.

**Q: What would you improve with more time?**
> A learned trust model instead of a fixed source-priority table; a human review queue for the borderline entity merges; and letting users correct a wrong merge directly in the graph.

---

*That's the whole project. If you can explain Sections 2, 3, 6, 7 and 9 in your own words, you can carry any interview or demo about it.*
