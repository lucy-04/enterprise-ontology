# Track A — infra tasks (Lakshay). Track B: do not edit; use just/ai.just.

hydradb_dir := env_var_or_default("HYDRADB_DIR", justfile_directory() + "/../hydradb")

# --- environment -----------------------------------------------------------

# Create the venv and install the frozen dependency set.
setup:
    uv sync
    @echo "ok: run 'source .venv/bin/activate' or prefix commands with 'uv run'"

# Lint and format.
lint:
    uv run ruff check src tests
    uv run ruff format --check src tests

fmt:
    uv run ruff format src tests

test:
    uv run pytest -q

# --- hydradb ---------------------------------------------------------------

# Start the local HydraDB node in the foreground. Leave it running; it does not return.
db-up:
    #!/usr/bin/env bash
    set -euo pipefail
    cd "{{ hydradb_dir }}"
    mkdir -p .hydradb/store .hydradb/cache
    printf '%s\n' 'local-development-token-32-bytes' > .hydradb/auth-token
    export CLOUD_PROVIDER=local
    export LOCAL_PATH="$PWD/.hydradb/store"
    export GRAPH_NAMESPACE=default
    export GRAPH_ID=default
    export GRAPH_CELL_ID=cell-0
    export GRAPH_CELLS=cell-0
    export GRAPH_NODE_ID=node-0
    export GRAPH_BOLT_NODE_ADDRESSES=node-0=127.0.0.1:7687
    export GRAPH_ADVERTISED_BOLT_ADDR=127.0.0.1:7687
    export GRAPH_DATA_CACHE_DIR="$PWD/.hydradb/cache"
    export GRAPH_AUTH_TOKEN_FILE="$PWD/.hydradb/auth-token"
    export GRAPH_ALLOW_PLAINTEXT=true
    # Required: graph-node's async query futures exceed the default thread
    # stack. Without this the node serves /readyz then aborts on first query.
    export RUST_MIN_STACK=33554432
    # macOS: cargo is invoked directly so it does not inherit hydradb's justfile exports.
    if command -v brew >/dev/null; then
      export BINDGEN_EXTRA_CLANG_ARGS="-I$(brew --prefix)/include"
      export LIBRARY_PATH="$(brew --prefix)/lib"
    fi
    echo "starting graph-node — bolt 127.0.0.1:7687, http 127.0.0.1:8443, admin 127.0.0.1:9090"
    cargo run --locked --features server-runtime --bin graph-node

# Prove the node actually works: round-trip a write, then a bounded path query.
# A listening port is not proof.
db-check:
    uv run python -m src.graph.check

# Verify HydraDB's own build prerequisites are discoverable.
db-native-check:
    cd "{{ hydradb_dir }}" && just native-check

# HydraDB's own smoke harness.
db-smoke:
    cd "{{ hydradb_dir }}" && just smoke

# --- pipeline: Track A stages ----------------------------------------------

# Download the EnterpriseRAG-Bench corpus and questions.
fetch-data *ARGS:
    uv run python -m src.ingest.fetch {{ ARGS }}

# Download the Salesforce HERB dataset (entity-resolution spot check).
fetch-herb:
    uv run python -m src.ingest.fetch_herb

# raw .txt -> data/normalized/{source}/*.parquet
normalize *ARGS:
    uv run python -m src.ingest.normalize {{ ARGS }}

# normalized -> SQLite FTS5 + local vector index
index *ARGS:
    uv run python -m src.index.build {{ ARGS }}

# entities + edges -> HydraDB (drops and reloads by design)
load *ARGS:
    uv run python -m src.graph.load {{ ARGS }}

# Full pipeline, both tracks, in order.
pipeline: normalize index extract resolve conflicts load

# --- serve + evaluate ------------------------------------------------------

# FastAPI + UI on http://localhost:8000 (needs 'just db-up' running).
serve:
    uv run uvicorn src.api.main:app --reload --port 8000

# Run the 500 benchmark questions and score them.
eval *ARGS:
    uv run python -m src.eval.run {{ ARGS }}
