# API Overview

NL2SQL provides a two-tier API architecture for flexible integration:

## Two-Tier API Architecture

### 1. Core API (Python)
- **Location**: Core package (`nl2sql-core`)
- **Interface**: Direct Python class interface (`NL2SQL` class and low-level functions)
- **Use Case**: Direct Python integration, embedded applications
- **Access**: Import and use directly in Python code

### 2. REST API (HTTP)
- **Location**: API package (`nl2sql-api`)
- **Interface**: HTTP REST endpoints
- **Use Case**: Remote clients, web applications, TypeScript CLI
- **Access**: HTTP requests to API endpoints

Both APIs provide access to the same underlying NL2SQL engine functionality, allowing flexible integration options.

## Core API (Low-Level)

The low-level Python API surface is centered on:

- `NL2SQLContext` (initialization)
- `run_with_graph()` (pipeline execution)

```python
from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.runtime import run_with_graph

ctx = NL2SQLContext()
result = run_with_graph(ctx, "Example query")
```

## High-Level Core API

The high-level Python API provides a cleaner interface through the `NL2SQL` class:

```python
from nl2sql import NL2SQL

engine = NL2SQL()
result = engine.run_query("Example query")
```

## REST API

The REST API provides HTTP endpoints for remote access:

- `POST /api/v1/query` - Execute natural language queries
- `GET /api/v1/schema/{datasource_id}` - Get schema information
- `GET /api/v1/schema` - List available datasources
- `GET /api/v1/health` - Health check
- `GET /api/v1/ready` - Readiness check

## Supporting contracts

- `GraphState` (`nl2sql.pipeline.state.GraphState`)
- `ExecutorRequest` / `ExecutorResponse` (`nl2sql.execution.contracts`)
- `ResultFrame` (`nl2sql_adapter_sdk.contracts`)

## Source references

- Context: `packages/core/src/nl2sql/context.py`
- Runtime: `packages/core/src/nl2sql/pipeline/runtime.py`
- Public API: `packages/core/src/nl2sql/public_api.py`
- API Package: `packages/api/src/nl2sql_api/`
