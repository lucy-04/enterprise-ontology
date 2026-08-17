"""FastAPI service.  `just serve`

Track A task A6 — the thin layer between the UI and everything else. It owns no
logic: /ask delegates to Track B's router, which is the single A -> B call
surface (CLAUDE.md §14.3).
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Enterprise Ontology", version="0.1.0")


class AskRequest(BaseModel):
    question: str
    question_id: str = "adhoc"


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ask")
def ask(req: AskRequest) -> dict:
    """Answer a question, returning the answer plus its full trace.

    The trace is what makes the demo legible: which route was taken, which
    documents were retrieved, which entities and paths were involved, which
    conflicts were surfaced, and whether the abstention gate passed.
    """
    raise NotImplementedError("A6 — wire to src.agent.router.answer()")


@app.get("/entity/{canonical_id}")
def entity(canonical_id: str) -> dict:
    """A resolved entity with every alias — the entity-resolution demo view."""
    raise NotImplementedError("A6")


@app.get("/doc/{doc_id}")
def doc(doc_id: str) -> dict:
    """One normalized source document, for the provenance panel."""
    raise NotImplementedError("A6")


@app.get("/subgraph")
def subgraph(ids: str) -> dict:
    """Nodes and edges around the given entity ids, for the graph view.

    Includes superseded edges so the UI can draw conflicts.
    """
    raise NotImplementedError("A6")
