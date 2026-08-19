# Fixes needed on Track B's side

**From:** Track A (Lakshay) · **For:** Track B (Shaurya) · **Written:** 2026-08-20

Four issues found while wiring Track A's real `GraphClient` (A3/A4/A5) and the
API/UI (A6/A7) to Track B's router. Every one of them is in a file Track B owns
(`src/agent/`, `src/llm/`, `tests/test_agent.py`), so per `CLAUDE.md` §14.1
Track A has **not** touched them.

They are ordered by impact. **#1 is demo-blocking and is a one-line change.**

| # | Issue | Impact | Size |
|---|---|---|---|
| 1 | Answers print raw entity ids instead of names | **Demo-blocking**, hurts Correctness on every graph question | 1 line |
| 2 | `test_router_never_raises_against_unimplemented_client` fails | Red suite | ~3 lines |
| 3 | Stale default model in the LLM adapter | Silent breakage for anyone without `.env` | 1 line |
| 4 | Offline grader waves everything through | Only bites with no API key | judgement call |

---

## 1. Answers contain raw entity ids instead of names — **demo-blocking**

### What you see

Ask the marquee conflict question through the API or the UI:

```
Q: Which team is Alex on now?
A: 8a08a7908de8`
```

The answer is a fragment of an internal id. This is *the* question the demo
video opens the conflict section with, so it has to read properly.

### Why it happens

`src/agent/router.py` builds the id → name map **only from entities the question
itself resolved**:

```python
# src/agent/router.py:56-60
trace.entity_ids = [e.canonical_id for e in resolved]
name_of = {e.canonical_id: e.canonical_name for e in resolved}

def naming(cid: str) -> str:
    return name_of.get(cid, cid)          # <-- falls back to the raw id
```

For `"Which team is Alex on now?"`, `question_entities()` finds `Alex`, so
`resolved` contains the Alex Person node — and nothing else. The *teams* on the
other end of the `MEMBER_OF` edges were never resolved, so they are not in
`name_of`, and `naming()` returns the id verbatim.

`format_edge_fact` then builds the evidence line that goes to the LLM
(`src/agent/synthesize.py:41-42`):

```python
src, dst = name_of(edge.src_canonical_id), name_of(edge.dst_canonical_id)
line = f"{src} {edge.rel_type} {dst}"
```

which produces exactly this, verified against the live graph:

```
alex MEMBER_OF ent_5eb38a08a7908de8 (current) [source: slack, doc dsid_001f5a...]
alex MEMBER_OF ent_3fcb03b07004b38e (was true until 2026-03-15, now superseded) [...]
```

The LLM is handed an opaque hash where the team name should be, so it echoes the
hash. It is behaving correctly given bad input.

You already flagged this in `progress/track-b.md` (B5/B6 session notes):

> *"`format_edge_fact` names the src entity but shows the dst as its id when the
> dst isn't in the resolved set — the GraphClient interface has no
> `get_entity(cid)`."*

### The fix

**`GraphClient.get_entity(cid)` now exists** — Track A added it in A5 specifically
for this. It returns a full `Entity` (with aliases) or `None`, and is already
used by the API's `/api/facts` endpoint, which is why the UI's conflict panel
renders names correctly while the answer sentence does not.

In `src/agent/router.py`, replace the `naming` closure at lines 59-60:

```python
    def naming(cid: str) -> str:
        return name_of.get(cid, cid)
```

with a version that falls back to a graph lookup, memoising into the same dict
so each id costs at most one query:

```python
    def naming(cid: str) -> str:
        """Resolve an id to a name.

        Entities on the *far* end of an edge were never resolved from the
        question, so they are absent from name_of. Without this fallback the
        LLM is handed a raw canonical id and repeats it back verbatim.
        """
        if cid not in name_of:
            entity = _safe(lambda: client.get_entity(cid))
            name_of[cid] = entity.canonical_name if entity else cid
        return name_of[cid]
```

`_safe` is already defined at the top of the module, so a client that does not
implement `get_entity` still degrades instead of raising.

### Expected result

```
alex MEMBER_OF Eng-Oncall (current) [source: slack, doc dsid_001f5a...]
alex MEMBER_OF Support (was true until 2026-03-15, now superseded) [...]
```

and an answer along the lines of *"Alex is on Eng-Oncall now; they were on
Support until 15 March 2026."* — which is the whole conflict-resolution story
the brief asks for, in one sentence with both sides and a date.

### Cost

One `get_entity` call per distinct unnamed id, cached in `name_of` for the rest
of the request. In practice a handful of extra Bolt round-trips per question,
each a few milliseconds. No LLM calls.

### How to verify

```bash
just db-up          # terminal 1
uv run python -c "
from src.graph.client import GraphClient
from src.agent.router import _gather
from src.common.schemas import AnswerTrace
c = GraphClient(); t = AnswerTrace()
_, facts = _gather('Which team is Alex on now?', 'conflict', c, t)
print('\n'.join(facts))
c.close()"
```

Every line should read `alex MEMBER_OF Eng-Oncall`, not `alex MEMBER_OF ent_...`.

---

## 2. `tests/test_agent.py::test_router_never_raises_against_unimplemented_client` now fails

### What you see

```
FAILED tests/test_agent.py::test_router_never_raises_against_unimplemented_client
assert (False)
 +  where False = AnswerResult(answer='Clauses + burn: daily scratch...', abstained=False).abstained
```

### Why it happens

The test's premise is stated in its own comment (`tests/test_agent.py:47`):

```python
# base GraphClient raises NotBuiltYetError from every method
r = answer("anything at all?", GraphClient(), "qX")
assert r.abstained and r.document_ids == []
```

That was true while Layer 1 was a stub. **A3 has landed**, so `GraphClient.search()`
and `.get_docs()` are real: a bare `GraphClient()` now hits the live SQLite +
vector index and returns actual documents for `"anything at all?"`. The router
finds evidence, so it answers rather than abstains — correctly.

The test is asserting yesterday's architecture, not a regression.

### Suggested fix

Split it into the two things it was really protecting, and make the "unbuilt
client" genuinely unbuilt rather than assuming the real one is:

```python
def test_router_never_raises_against_a_broken_client():
    """A client whose every method fails must produce an abstention, not a
    crash — one unhandled error would zero the whole 500-question eval run."""
    class Broken(GraphClient):
        def search(self, *a, **k): raise RuntimeError("boom")
        def get_docs(self, *a, **k): raise RuntimeError("boom")
        def find_entity(self, *a, **k): raise RuntimeError("boom")
        def neighbors(self, *a, **k): raise RuntimeError("boom")
        def paths(self, *a, **k): raise RuntimeError("boom")
        def facts_about(self, *a, **k): raise RuntimeError("boom")

    r = answer("anything at all?", Broken(), "qX")
    assert r.abstained and r.document_ids == []
    assert r.answer  # a real string, not a crash
```

That keeps the property worth guarding (the router never raises) and stops the
test breaking every time Track A implements another method.

---

## 3. Stale default model in the LLM adapter

### What you see

With `LLM_PROVIDER=gemini` and no `LLM_MODEL_*` set:

```
google.genai.errors.ClientError: 404 NOT_FOUND.
'This model models/gemini-2.0-flash is no longer available.
 Please update your code to use models/gemini-3.6-flash'
```

### Why it happens

`src/llm/adapter.py:44`:

```python
_DEFAULT_MODELS = {
    "gemini": ("gemini-2.0-flash", "gemini-2.0-flash-lite"),
```

Both Gemini 2.0 models have been retired.

### Important: do *not* switch the default to `gemini-flash-latest`

That is the obvious replacement and it is **worse than the broken one**, because
it fails silently instead of loudly.

`gemini-flash-latest` is a thinking model. At the small `max_tokens` this
codebase uses (`grade()` passes `max_tokens=120`), its internal reasoning
consumes the entire output budget and the response comes back **empty**.
Measured on this key:

| model | non-empty responses at `max_output_tokens=120` |
|---|---|
| `gemini-flash-latest` | **0 / 3** |
| `gemini-flash-lite-latest` | 3 / 3 |
| `gemini-3.5-flash` | 3 / 3 |
| `gemini-2.5-flash-lite` | 3 / 3 |

An empty response hits this branch in `grade()` (`src/agent/synthesize.py:71-72`):

```python
if not verdict:
    return True, "grader unavailable, proceeding"
```

so **the abstention gate silently passes everything** while the logs look
healthy. That would have quietly cost all 20 `info_not_found` questions plus
Correctness elsewhere.

### Suggested fix

```python
    "gemini": ("gemini-3.5-flash", "gemini-flash-lite-latest"),
```

Track A has already set `LLM_MODEL_STRONG=gemini-3.5-flash` and
`LLM_MODEL_CHEAP=gemini-flash-lite-latest` in `.env` and `.env.example`, so this
only affects someone running without those vars — but that is exactly the person
who will be most confused by it.

**Also:** `data/cache/llm/` was cleared, because it had cached empty responses
from the broken model and would have served them back forever. If you hit odd
empty results, clear it again.

---

## 4. The offline grader waves everything through

### Status: not currently biting, but worth a look

`src/agent/synthesize.py:61-63`:

```python
if not llm.available:
    # offline heuristic: having real context is enough to attempt an answer.
    return True, "heuristic: evidence present (no LLM grader configured)"
```

This was a reasonable default when Layer 1 did not exist and `context` was
usually empty. Now that search covers 511,958 documents, **it always returns
something** for any query, so the offline path never abstains. Measured before
the API key was added:

- `info_not_found` questions: abstained **0 / 8**
- literal gibberish (`"zzqqxx vlorptang mimsy borogove"`): answered confidently
  with a citation

With the Gemini key set this branch is not reached and abstention is **8 / 8**,
so it is no longer urgent. But it is a live trap if quota runs out mid-eval —
the run would not fail, it would just quietly stop abstaining.

### Options

- Cheapest: require some lexical overlap between the question and the retrieved
  context before passing, rather than passing on non-empty context alone.
- Or: treat "no LLM available" as a **failed** grade for `info_not_found`-shaped
  questions, accepting a few false abstentions over false confidence.
- Or: leave as is and treat quota exhaustion as a hard stop, which is defensible
  — just make it a deliberate decision rather than an accident.

---

## Summary for a five-minute pass

```
1. src/agent/router.py:59-60      one line     ← do this one, it unblocks the demo
2. tests/test_agent.py:46-50      ~3 lines
3. src/llm/adapter.py:44          one line
4. src/agent/synthesize.py:61-63  judgement call, not urgent
```

Nothing here is a design problem — the pipeline works end to end. #1 is a
plumbing gap that only appeared once a real graph client existed to expose it,
and the method needed to fix it now exists.
