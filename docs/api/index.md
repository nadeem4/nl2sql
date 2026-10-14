# API Overview

NL2SQL exposes two API surfaces: a Python Core API for in-process use and a REST
API for remote clients. Use the links below for the full technical references.

## Core API (Python)

- **Location**: Core package (`nl2sql`)
- **Interface**: the `NL2SQL` class: `run_query` and `stream_query` at the top
  level, everything else in a namespace (`engine.datasources`, `engine.index`,
  `engine.llm`, `engine.policies`, `engine.curated`, `engine.results`,
  `engine.traces`, `engine.benchmark`)
- **Use Case**: Embedded/SDK use in Python applications
- **Reference**: Core API index and per-namespace references:
  - [Core API index](core/public-facade.md)
  - [Query API](core/query.md) (`run_query`, `stream_query`, `engine.results`)
  - [Datasource API](core/datasource.md) (`engine.datasources`)
  - [Indexing API](core/indexing.md) (`engine.index`)
  - [LLM API](core/llm.md) (`engine.llm`)
  - [Policies API](core/policies.md) (`engine.policies`)

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
- `ResultFrame` (`nl2sql_sdk.contracts`)

## Source references

- Runtime: `packages/nl2sql/src/nl2sql/runtime.py`
- Running the graph: `packages/nl2sql/src/nl2sql/pipeline/runtime.py`
- Public API: `packages/nl2sql/src/nl2sql/public_api.py`, with the namespaces in
  `packages/nl2sql/src/nl2sql/api/`
- API Package: `packages/api/src/nl2sql_api/`
