"""Config loading. Shared by both tracks — frozen night one (CLAUDE.md §14.1).

Secrets and machine-specific paths come from .env; tuning comes from
config.yaml. Everything is read through here so neither track hardcodes a path
or a threshold.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(REPO_ROOT / ".env")


@lru_cache(maxsize=1)
def settings() -> dict[str, Any]:
    """Parsed config.yaml."""
    with open(REPO_ROOT / "config.yaml") as fh:
        return yaml.safe_load(fh)


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    return int(raw) if raw else default


def data_dir() -> Path:
    return (REPO_ROOT / os.environ.get("DATA_DIR", "./data")).resolve()


def path(*parts: str) -> Path:
    """A path under DATA_DIR, with parent directories created."""
    p = data_dir().joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@lru_cache(maxsize=1)
def ontology() -> dict[str, Any]:
    """Parsed ontology/ontology.yaml (Track B owns the file)."""
    with open(REPO_ROOT / "ontology" / "ontology.yaml") as fh:
        return yaml.safe_load(fh)


def hydra_config() -> dict[str, str]:
    return {
        "uri": os.environ.get("HYDRA_URI", "bolt://127.0.0.1:7687"),
        "user": os.environ.get("HYDRA_USER", ""),
        "password": os.environ.get("HYDRA_PASSWORD", ""),
        "http": os.environ.get("HYDRA_HTTP", "http://127.0.0.1:8443"),
        "graph": os.environ.get("HYDRA_GRAPH", "default"),
        "namespace": os.environ.get("HYDRA_NAMESPACE", "default"),
        "cell_id": os.environ.get("HYDRA_CELL_ID", "cell-0"),
    }
