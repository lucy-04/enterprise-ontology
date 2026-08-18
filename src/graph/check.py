"""Prove the HydraDB node actually works.  `just db-check`

Run while `just db-up` is going in another terminal.

A listening port is not proof — this round-trips a write, reads it back over
both HTTP and Bolt, then runs algo.SPpaths, because native bounded-path
traversal is what the whole Layer 2 design depends on (CLAUDE.md §5).

Every query here is written against HydraDB's *actual* OpenCypher subset, which
is much narrower than plain Cypher. See CLAUDE.md §5.1 — the constraints are not
optional style preferences, they are parse-time rejections.

Exit 0 means HydraDB is genuinely usable.
"""

from __future__ import annotations

import sys

import httpx

from src.common.config import hydra_config

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

# Reserved integer ids for this check. Node ids MUST be non-negative integers.
A, B, C = 900000001, 900000002, 900000003


def _query(cfg: dict[str, str], cypher: str) -> dict:
    resp = httpx.post(
        f"{cfg['http']}/v1/graphs/{cfg['graph']}/query",
        headers={
            "Authorization": f"Bearer {cfg['password']}",
            "X-Graph-Namespace": cfg["namespace"],
            "Content-Type": "application/json",
        },
        json={"cell_id": cfg["cell_id"], "query": cypher},
        timeout=30.0,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def check_http(cfg: dict[str, str]) -> bool:
    """Write and read a small graph over the HTTP API.

    Built as two separate one-hop CREATEs: HydraDB's CREATE takes relationship
    paths only, and only one hop each, so a 3-node chain in one statement is
    rejected.
    """
    try:
        for node in (A, B, C):
            _query(cfg, f"MATCH (n:HealthCheck {{id: {node}}}) DETACH DELETE n")

        _query(cfg, f"CREATE (a:HealthCheck {{id: {A}, name: 'alice'}})"
                    f"-[:KNOWS {{src: 'check'}}]->"
                    f"(b:HealthCheck {{id: {B}, name: 'bob'}})")
        _query(cfg, f"CREATE (b:HealthCheck {{id: {B}}})"
                    f"-[:KNOWS {{src: 'check'}}]->"
                    f"(c:HealthCheck {{id: {C}, name: 'carol'}})")

        out = _query(cfg, f"MATCH (a:HealthCheck {{id: {A}}})-[:KNOWS]->(b) "
                          f"RETURN b.name AS name")
        if "bob" not in str(out):
            print(f"  {FAIL} HTTP round trip did not return 'bob': {str(out)[:300]}")
            return False
        print(f"  {PASS} HTTP write + read round-tripped")
        return True
    except Exception as exc:  # noqa: BLE001 - spike script, report anything
        print(f"  {FAIL} HTTP: {type(exc).__name__}: {exc}")
        return False


def check_bolt(cfg: dict[str, str]) -> bool:
    """Connect with the Neo4j driver — the path GraphClient uses.

    Auth on a local plaintext node is the dev token as the password.
    """
    from neo4j import GraphDatabase

    candidates = [
        ("token as password", ("neo4j", cfg["password"])),
        ("user + token", (cfg["user"] or "hydra", cfg["password"])),
        ("no auth", None),
    ]
    for label, auth in candidates:
        try:
            driver = GraphDatabase.driver(cfg["uri"], auth=auth)
            with driver.session() as session:
                rec = session.run(
                    f"MATCH (a:HealthCheck {{id: {A}}})-[:KNOWS]->(b) "
                    f"RETURN b.name AS name"
                ).single()
            driver.close()
            if rec and rec.get("name") == "bob":
                print(f"  {PASS} Bolt connected and read back ({label})")
                return True
            print(f"  {FAIL} Bolt connected ({label}) but returned {rec}")
            return False
        except Exception as exc:  # noqa: BLE001
            print(f"       tried {label}: {type(exc).__name__}: {str(exc)[:100]}")
    print(f"  {FAIL} Bolt: no auth combination worked")
    return False


def check_paths(cfg: dict[str, str]) -> bool:
    """The one that matters: native bounded-path traversal.

    Config keys are sourceNode / targetNode taking integer node ids — not the
    sourceValues form originally recorded in CLAUDE.md §5.
    """
    query = (
        f"CALL algo.SPpaths({{sourceNode: {A}, targetNode: {C}, "
        f"relTypes: ['KNOWS'], relDirection: 'both', maxLen: 3, pathCount: 5}}) "
        f"YIELD path RETURN path"
    )
    try:
        out = _query(cfg, query)
        rows = out.get("rows") or []
        if not rows:
            print(f"  {FAIL} algo.SPpaths returned no path {A} -> {C}")
            return False
        if "carol" not in str(out):
            print(f"  {FAIL} algo.SPpaths path did not reach carol: {str(out)[:300]}")
            return False
        print(f"  {PASS} algo.SPpaths returned a 2-hop path alice -> bob -> carol")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  {FAIL} algo.SPpaths: {type(exc).__name__}: {str(exc)[:300]}")
        return False


def check_varlen(cfg: dict[str, str]) -> bool:
    """Variable-length MATCH, the other multi-hop route. Max bound is required."""
    try:
        out = _query(cfg, f"MATCH (a:HealthCheck {{id: {A}}})-[:KNOWS*1..3]->(v) "
                          f"RETURN v.id AS id")
        n = len(out.get("rows") or [])
        if n < 2:
            print(f"  {FAIL} variable-length match returned {n} rows, expected 2")
            return False
        print(f"  {PASS} variable-length traversal *1..3 returned {n} nodes")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  {FAIL} variable-length match: {type(exc).__name__}: {str(exc)[:200]}")
        return False


def main() -> int:
    cfg = hydra_config()
    print(f"checking HydraDB at {cfg['http']} / {cfg['uri']}\n")

    results = {
        "http round trip": check_http(cfg),
        "bolt driver": check_bolt(cfg),
        "algo.SPpaths": check_paths(cfg),
        "variable-length match": check_varlen(cfg),
    }

    # Leave no test data behind.
    try:
        for node in (A, B, C):
            _query(cfg, f"MATCH (n:HealthCheck {{id: {node}}}) DETACH DELETE n")
    except Exception:  # noqa: BLE001
        pass

    print()
    if all(results.values()):
        print("HydraDB is working. Bolt, HTTP and native path traversal all verified.")
        return 0

    failed = [name for name, ok in results.items() if not ok]
    print(f"FAILED: {', '.join(failed)}")
    print("\nIs the node running? Start it with `just db-up` in another terminal.")
    print("Cypher rejected? Read CLAUDE.md §5.1 — the supported subset is narrow.")
    print("Record what failed in progress/track-a.md before trying a fallback.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
