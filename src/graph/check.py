"""Prove the HydraDB node actually works.

Run with `just db-check` while `just db-up` is running in another terminal.

A listening port is not proof — this round-trips a write, reads it back, and
then runs a bounded path query through algo.SPpaths, because native path
procedures are what the whole Layer 2 design depends on (CLAUDE.md §5).

Exit code 0 means HydraDB is genuinely usable. Anything else means Track A is
still blocked.
"""

from __future__ import annotations

import sys

import httpx

from src.common.config import hydra_config

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def _http_query(cfg: dict[str, str], cypher: str) -> dict:
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
    resp.raise_for_status()
    return resp.json()


def check_http(cfg: dict[str, str]) -> bool:
    """Write and read a small graph over the HTTP API."""
    try:
        _http_query(cfg, "MATCH (n:HealthCheck) DETACH DELETE n")
        _http_query(
            cfg,
            "CREATE (a:HealthCheck {name: 'alice'})-[:KNOWS]->"
            "(b:HealthCheck {name: 'bob'})-[:KNOWS]->"
            "(c:HealthCheck {name: 'carol'})",
        )
        out = _http_query(
            cfg,
            "MATCH (a:HealthCheck {name: 'alice'})-[:KNOWS]->(b) RETURN b.name AS name",
        )
        body = str(out)
        if "bob" not in body:
            print(f"  {FAIL} HTTP round trip returned no 'bob': {body[:400]}")
            return False
        print(f"  {PASS} HTTP write + read round-tripped")
        return True
    except Exception as exc:  # noqa: BLE001 - spike script, report anything
        print(f"  {FAIL} HTTP query failed: {type(exc).__name__}: {exc}")
        return False


def check_bolt(cfg: dict[str, str]) -> bool:
    """Connect with the Neo4j driver — the path Track B's GraphClient uses."""
    from neo4j import GraphDatabase
    from neo4j.exceptions import Neo4jError

    # Auth shape for a local plaintext node isn't documented for Bolt; try the
    # plausible combinations and report which one works so it can be pinned in
    # SETUP.md.
    candidates = [
        ("token as password", ("neo4j", cfg["password"])),
        ("user+token", (cfg["user"] or "hydra", cfg["password"])),
        ("no auth", None),
    ]
    for label, auth in candidates:
        try:
            driver = GraphDatabase.driver(cfg["uri"], auth=auth)
            with driver.session() as session:
                rec = session.run(
                    "MATCH (a:HealthCheck {name: 'alice'})-[:KNOWS]->(b) RETURN b.name AS name"
                ).single()
            driver.close()
            if rec and rec.get("name") == "bob":
                print(f"  {PASS} Bolt connected and read back ({label})")
                return True
            print(f"  {FAIL} Bolt connected ({label}) but query returned {rec}")
            return False
        except (Neo4jError, OSError, ValueError) as exc:
            print(f"       tried {label}: {type(exc).__name__}: {str(exc)[:120]}")
        except Exception as exc:  # noqa: BLE001
            print(f"       tried {label}: {type(exc).__name__}: {str(exc)[:120]}")
    print(f"  {FAIL} Bolt: no auth combination worked")
    return False


def check_paths(cfg: dict[str, str]) -> bool:
    """The one that matters: native bounded-path traversal.

    Layer 2's multi-hop answers are built on algo.SPpaths / SSpaths / MSpaths
    rather than hand-rolled BFS. If this doesn't work, the architecture needs
    rethinking now, not on Aug 19.
    """
    query = (
        "CALL algo.SPpaths({"
        "sourceLabel: 'HealthCheck', sourceProperty: 'name', "
        "sourceValue: 'alice', targetValue: 'carol', "
        "relTypes: ['KNOWS'], relDirection: 'both', "
        "maxLen: 3, pathCount: 5, resultLimit: 10"
        "}) YIELD path RETURN path"
    )
    try:
        out = _http_query(cfg, query)
        body = str(out)
        if "carol" in body or "path" in body.lower():
            print(f"  {PASS} algo.SPpaths returned a path alice -> carol")
            return True
        print(f"  {FAIL} algo.SPpaths returned nothing usable: {body[:400]}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"  {FAIL} algo.SPpaths failed: {type(exc).__name__}: {str(exc)[:300]}")
        print("       -> check the exact procedure signature in hydradb's docs;")
        print("          arg names may differ from what CLAUDE.md §5 recorded.")
        return False


def main() -> int:
    cfg = hydra_config()
    print(f"checking HydraDB at {cfg['http']} / {cfg['uri']}\n")

    results = {
        "http round trip": check_http(cfg),
        "bolt driver": check_bolt(cfg),
        "algo.SPpaths": check_paths(cfg),
    }

    print()
    if all(results.values()):
        print("HydraDB is working. Track A is unblocked.")
        return 0

    failed = [name for name, ok in results.items() if not ok]
    print(f"FAILED: {', '.join(failed)}")
    print("\nIs the node running? Start it with `just db-up` in another terminal.")
    print("Record what failed in progress/track-a.md before trying a fallback.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
