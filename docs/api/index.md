# API Overview

The primary Python API surface for running NL2SQL is small and centered on:

- `NL2SQLContext` (initialization)
- `run_with_graph()` (pipeline execution)

## Core API

```python
from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.runtime import run_with_graph

ctx = NL2SQLContext()
result = run_with_graph(ctx, "Example query")
```

## Supporting contracts

- `GraphState` (`nl2sql.pipeline.state.GraphState`)
- `ExecutorRequest` / `ExecutorResponse` (`nl2sql.execution.contracts`)
- `ResultFrame` (`nl2sql_adapter_sdk.contracts`)

## Source references

- Context: `packages/core/src/nl2sql/context.py`
- Runtime: `packages/core/src/nl2sql/pipeline/runtime.py`
