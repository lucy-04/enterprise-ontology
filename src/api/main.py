"""FastAPI service.  `just serve`

Track A task A6 — the thin layer between the UI and everything else. It owns no
logic: /ask delegates to Track B's router, which is the single A -> B call
surface (CLAUDE.md §14.3).

Everything it returns is designed to be *shown*, not just consumed. The demo has
to make three things visible (CLAUDE.md §6):

  * that "Sam", "@soham" and "sam@redwood.com" resolved to one person,
  * that a contradiction was surfaced with both sides, dated and sourced,
    rather than silently resolved,
  * that an unanswerable question was declined rather than hallucinated.

So every answer ships its trace — route taken, documents retrieved, entities
resolved, paths walked, conflicts found, and the grade decision — and the
serialisers below flatten the dataclasses into exactly the shape the page
renders.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path as FilePath
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.common.schemas import AnswerResult, Edge, Entity, NormalizedDoc, Path
from src.graph.client import GraphClient

app = FastAPI(title="Enterprise Ontology", version="1.0.0")

UI_DIR = FilePath(__file__).resolve().parents[1] / "ui"


@lru_cache(maxsize=1)
def client() -> GraphClient:
    """One shared client. Opening Bolt and memory-mapping the vector index per
    request would dominate the response time."""
    return GraphClient()


class AskRequest(BaseModel):
    question: str
    question_id: str = "adhoc"


# ---------------------------------------------------------------------------
# serialisation — dataclasses -> what the page draws
# ---------------------------------------------------------------------------
def _edge_json(edge: Edge) -> dict[str, Any]:
    return {
        "edge_id": edge.edge_id,
        "src": edge.src_canonical_id,
        "dst": edge.dst_canonical_id,
        "rel_type": edge.rel_type,
        "stated_at": edge.stated_at.isoformat() if edge.stated_at else None,
        "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
        "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
        "is_current": edge.valid_to is None,
        "source_type": edge.source_type,
        "source_doc_ids": edge.source_doc_ids,
        "confidence": edge.confidence,
        "contested": edge.contested,
        "superseded_by": edge.superseded_by,
    }


def _entity_json(entity: Entity) -> dict[str, Any]:
    return {
        "canonical_id": entity.canonical_id,
        "entity_type": entity.entity_type,
        "canonical_name": entity.canonical_name,
        "aliases": entity.aliases,
        "handles": entity.handles,
        "emails": entity.emails,
        "mention_count": entity.mention_count,
        "source_types": entity.source_types,
        # Every distinct surface form in one list — this is the entity-resolution
        # picture the demo opens on.
        "surface_forms": sorted({*entity.aliases, *entity.handles, *entity.emails,
                                 entity.canonical_name} - {""}),
    }


def _doc_json(doc: NormalizedDoc, body: bool = True) -> dict[str, Any]:
    return {
        "doc_id": doc.doc_id,
        "source_type": doc.source_type,
        "title": doc.title,
        "body": doc.body if body else doc.body[:400],
        "timestamp": doc.timestamp.isoformat() if doc.timestamp else None,
        "author_refs": doc.author_refs,
        "mention_refs": doc.mention_refs,
        "path": doc.path,
    }


def _path_json(path: Path) -> dict[str, Any]:
    return {
        "start": _entity_json(path.start),
        "length": path.length,
        "doc_ids": path.doc_ids,
        "steps": [{"edge": _edge_json(s.edge), "to": _entity_json(s.to_entity)}
                  for s in path.steps],
    }


def _answer_json(result: AnswerResult) -> dict[str, Any]:
    trace = result.trace
    return {
        "question_id": result.question_id,
        "answer": result.answer,
        "document_ids": result.document_ids,
        "abstained": result.abstained,
        "confidence": result.confidence,
        "trace": {
            "route": trace.route,
            "retrieved_doc_ids": trace.retrieved_doc_ids,
            "entity_ids": trace.entity_ids,
            "paths": [_path_json(p) for p in trace.paths],
            "conflicts": [{
                "current": _edge_json(c.current),
                "superseded": [_edge_json(e) for e in c.superseded],
                "explanation": c.explanation,
            } for c in trace.conflicts],
            "grade_passed": trace.grade_passed,
            "grade_reason": trace.grade_reason,
            "retries": trace.retries,
            "llm_calls": trace.llm_calls,
        },
    }


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------
@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    """What is actually loaded. Shown in the UI header so a viewer can see the
    scale the answers are drawn from."""
    graph = client()
    out: dict[str, Any] = {"documents": 0, "entities": 0, "aliases": 0, "edges": 0}
    try:
        out["documents"] = graph.index.count()
    except Exception:
        pass
    for key, cypher in (
        ("entities", "MATCH (n:Person) RETURN count(*) AS c"),
        ("aliases", "MATCH (a:Alias) RETURN count(*) AS c"),
    ):
        try:
            rows = graph.cypher(cypher)
            out[key] = rows[0]["c"] if rows else 0
        except Exception:
            pass
    return out


@app.post("/ask")
def ask(req: AskRequest) -> dict[str, Any]:
    """Answer a question, returning the answer plus its full trace.

    The trace is what makes the demo legible: which route was taken, which
    documents were retrieved, which entities and paths were involved, which
    conflicts were surfaced, and whether the abstention gate passed.
    """
    from src.agent.router import answer as route_answer

    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    return _answer_json(route_answer(question, client(), req.question_id))


@app.get("/api/search")
def search(q: str = Query(...), k: int = 10,
           sources: str | None = None) -> dict[str, Any]:
    """Raw Layer 1 search, no LLM. Useful for demoing retrieval on its own."""
    source_list = [s for s in (sources or "").split(",") if s] or None
    hits = client().search(q, k=k, sources=source_list)
    return {"query": q, "hits": [{
        "doc_id": h.doc_id, "source_type": h.source_type, "title": h.title,
        "snippet": h.snippet, "score": h.score,
        "timestamp": h.timestamp.isoformat() if h.timestamp else None,
    } for h in hits]}


@app.get("/entity/{canonical_id}")
def entity(canonical_id: str) -> dict[str, Any]:
    """A resolved entity with every alias — the entity-resolution demo view."""
    found = client().get_entity(canonical_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no entity {canonical_id}")
    return _entity_json(found)


@app.get("/api/resolve")
def resolve(name: str = Query(...), type: str | None = None) -> dict[str, Any]:
    """Look an entity up by ANY surface form.

    The headline capability: "Sam", "@soham" and "sam@redwood.com" all land on
    the same node.
    """
    return {"query": name,
            "entities": [_entity_json(e) for e in client().find_entity(name, type)]}


@app.get("/api/facts/{canonical_id}")
def facts(canonical_id: str, rel_type: str | None = None) -> dict[str, Any]:
    """Facts about an entity, current and superseded.

    Superseded edges are included deliberately: a conflict answer has to show
    what changed and when, not just today's value.
    """
    graph = client()
    rel_types = [rel_type] if rel_type else [
        "MEMBER_OF", "WORKS_FOR", "OWNS", "HAS_ROLE", "ASSIGNED_TO", "REPORTS_TO"]
    edges: list[Edge] = []
    for rel in rel_types:
        edges.extend(graph.facts_about(canonical_id, rel))

    names: dict[str, str] = {}
    for edge in edges:
        for cid in (edge.src_canonical_id, edge.dst_canonical_id):
            if cid and cid not in names:
                found = graph.get_entity(cid)
                names[cid] = found.canonical_name if found else cid

    return {
        "canonical_id": canonical_id,
        "names": names,
        "current": [_edge_json(e) for e in edges if e.valid_to is None],
        "superseded": [_edge_json(e) for e in edges if e.valid_to is not None],
    }


@app.get("/doc/{doc_id}")
def doc(doc_id: str) -> dict[str, Any]:
    """One normalized source document, for the provenance panel."""
    docs = client().get_docs([doc_id])
    if not docs:
        raise HTTPException(status_code=404, detail=f"no document {doc_id}")
    return _doc_json(docs[0])


@app.get("/api/docs")
def docs(ids: str = Query(...)) -> dict[str, Any]:
    """Several documents at once — the citation list under an answer."""
    wanted = [d for d in ids.split(",") if d]
    return {"docs": [_doc_json(d, body=False) for d in client().get_docs(wanted)]}


@app.get("/subgraph")
def subgraph(ids: str = Query(...), max_len: int = 2) -> dict[str, Any]:
    """Nodes and edges around the given entity ids, for the graph view.

    Includes superseded edges so the UI can draw conflicts.

    Built from the native path procedures rather than a hand-rolled expansion,
    so what the picture shows is exactly what the graph engine traversed.
    """
    graph = client()
    seeds = [i for i in ids.split(",") if i]
    if not seeds:
        return {"nodes": [], "edges": []}

    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}

    def add_entity(entity: Entity, seed: bool = False) -> None:
        if entity.canonical_id and entity.canonical_id not in nodes:
            payload = _entity_json(entity)
            payload["seed"] = seed
            nodes[entity.canonical_id] = payload

    for seed_id in seeds:
        found = graph.get_entity(seed_id)
        if found is not None:
            add_entity(found, seed=True)
        for path in graph.paths([seed_id], [], max_len=max_len):
            add_entity(path.start)
            for step in path.steps:
                add_entity(step.to_entity)
                edges.setdefault(step.edge.edge_id or
                                 f"{step.edge.src_canonical_id}-{step.edge.rel_type}",
                                 _edge_json(step.edge))
        # Superseded edges never appear in a current-state traversal, but the
        # conflict story is the point of the demo, so pull them in explicitly.
        for rel in ("MEMBER_OF", "WORKS_FOR", "OWNS", "HAS_ROLE"):
            for edge in graph.facts_about(seed_id, rel):
                other = graph.get_entity(
                    edge.dst_canonical_id if edge.src_canonical_id == seed_id
                    else edge.src_canonical_id)
                if other is not None:
                    add_entity(other)
                edges.setdefault(edge.edge_id, _edge_json(edge))

    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


# ---------------------------------------------------------------------------
# static UI — mounted last so it cannot shadow an API route
# ---------------------------------------------------------------------------
if UI_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=UI_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(UI_DIR / "index.html")
