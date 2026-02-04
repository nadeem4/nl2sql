# API Overview

NL2SQL exposes two API surfaces: a Python Core API for in-process use and a REST
API for remote clients. Use the links below for the full technical references.

## Core API (Python)

- **Location**: Core package (`nl2sql-core`)
- **Interface**: Direct Python class interface (`NL2SQL` class and low-level functions)
- **Use Case**: Embedded/SDK use in Python applications
- **Reference**: Core API index and per-module references:
  - [Core API index](core/public-facade.md)
  - [Auth API](core/auth.md)
  - [Datasource API](core/datasource.md)
  - [LLM API](core/llm.md)
  - [Indexing API](core/indexing.md)
  - [Query API](core/query.md)
  - [Settings API](core/settings.md)
  - [Result API](core/result.md)

## REST API (FastAPI)

- **Location**: API package (`nl2sql-api`)
- **Interface**: HTTP REST endpoints
- **Use Case**: Remote clients, web applications, TypeScript CLI
- **Reference**: REST API index and per-route references:
  - [REST API index](rest/index.md)
  - [Health API](rest/health.md)
  - [Query API](rest/query.md)
  - [Datasource API](rest/datasource.md)
  - [LLM API](rest/llm.md)
  - [Indexing API](rest/indexing.md)

## Supporting contracts

- `GraphState` (`nl2sql.pipeline.state.GraphState`)
- `ExecutorRequest` / `ExecutorResponse` (`nl2sql.execution.contracts`)
- `ResultFrame` (`nl2sql_adapter_sdk.contracts`)

## Source references

- Context: `packages/core/src/nl2sql/context.py`
- Runtime: `packages/core/src/nl2sql/pipeline/runtime.py`
- Public API: `packages/core/src/nl2sql/public_api.py`
- API Package: `packages/api/src/nl2sql_api/`
