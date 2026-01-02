# Development Guide

How to contribute to the platform.

## Setup

```bash
# Windows PowerShell
./scripts/setup_dev.ps1
```

## Running Tests

We use `pytest`.

```bash
# Run Unit Tests (Fast)
python -m pytest packages/core/tests/unit

# Run Integration Tests (Requires Docker)
docker compose up -d
python -m pytest packages/core/tests/integration
```

## Adding a New Node

1. Create the node class in `packages/core/src/nl2sql/pipeline/nodes/`.
2. Implement `__call__(self, state: GraphState) -> Dict`.
3. Register it in `graph.py` or the relevant subgraph (`sql_agent.py`).
4. Add unit tests.
