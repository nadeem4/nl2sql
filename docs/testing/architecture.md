# Testing Architecture

Tests are organized by scope and live under `packages/**/tests`. The core engine has unit, integration, and end-to-end tests.

## Test layout

```text
packages/core/tests/
  unit/          # Node-level tests, registries, stores
  integration/   # Pipeline components with real data
  e2e/           # End-to-end flows
```

`pytest.ini` defines test paths and markers.

```mermaid
flowchart TD
    Unit[Unit Tests] --> Core[Core Components]
    Integration[Integration Tests] --> Pipeline[Pipeline Nodes]
    E2E[End-to-end Tests] --> Orchestration[Full Orchestration]
```

## Runtime coverage targets

- Pipeline nodes (`test_node_*.py`)
- Subgraph orchestration (`test_sql_agent_subgraph.py`)
- DAG layering (`test_graph_layers.py`)
- Registry and store behavior (`test_*_registry.py`, `test_schema_store.py`)

## Source references

- Test configuration: `pytest.ini`
- Core tests: `packages/core/tests/`
