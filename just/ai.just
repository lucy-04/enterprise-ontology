# Track B — AI tasks (Shaurya). Track A: do not edit; use just/infra.just.
#
# These are stubs so the task surface exists from night one. Fill in the module
# behind each one as you build it; the recipe names are referenced by
# 'just pipeline' in just/infra.just, so keep the names stable.

# normalized -> data/candidates/{mentions,relations}.parquet  (rule-based, no LLM)
extract *ARGS:
    uv run python -m src.extract.run {{ ARGS }}

# candidates -> data/resolved/entities.parquet  (Splink + LLM adjudication)
resolve *ARGS:
    uv run python -m src.resolve.run {{ ARGS }}

# resolved -> data/graph/edges.parquet  (bi-temporal conflict pass)
conflicts *ARGS:
    uv run python -m src.conflicts.run {{ ARGS }}

# Validate ontology/ontology.yaml against the schema contract.
ontology-check:
    uv run python -m src.extract.validate_ontology

# Ask a single question through the agent, printing the full trace.
ask QUESTION:
    uv run python -m src.agent.ask "{{ QUESTION }}"

# Show how much free-tier LLM quota has been consumed (cache-backed counters).
llm-budget:
    uv run python -m src.llm.budget
