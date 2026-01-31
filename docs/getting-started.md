# Getting Started

This guide walks through the minimal steps to run the NL2SQL pipeline from Python code. The runtime is configured via environment variables and the YAML/JSON files under `configs/`.

## Prerequisites

- Python 3.10+
- A configured datasource in `configs/datasources.yaml`
- An LLM configuration in `configs/llm.yaml`

## Install the core package

```bash
pip install -e packages/core
pip install -e packages/adapter-sdk
```

If you are using the SQLAlchemy adapter, install it as well:

```bash
pip install -e packages/adapter-sqlalchemy
```

## Configure the system

`NL2SQLContext` reads its configuration from these settings (see `nl2sql.common.settings.Settings`):

- `configs/datasources.yaml` (datasource definitions)
- `configs/llm.yaml` (agent model configuration)
- `configs/policies.json` (RBAC policies)
- `configs/secrets.yaml` (secret providers, optional)

Template examples exist in `configs/*.example.yaml` and `configs/*.example.json`.

## Index schema (required for retrieval)

Before running queries, index your datasource schema:

```python
from nl2sql.context import NL2SQLContext
from nl2sql.indexing.orchestrator import IndexingOrchestrator

ctx = NL2SQLContext()
orchestrator = IndexingOrchestrator(ctx)

for adapter in ctx.ds_registry.list_adapters():
    orchestrator.index_datasource(adapter)
```

## Run a query

```python
from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.runtime import run_with_graph

ctx = NL2SQLContext()
result = run_with_graph(ctx, "Top 5 customers by revenue last quarter?")

print(result.get("final_answer"))
print(result.get("errors"))
```

## Execution flag

`run_with_graph()` accepts an `execute` flag and passes it to `build_graph()`. The current graph builder does not branch on this flag, so any execution gating must be implemented inside nodes or executors.

## Next steps

- See `configuration/system.md` for configuration details and environment variable mapping.
- See `deployment/architecture.md` for production deployment guidance.
