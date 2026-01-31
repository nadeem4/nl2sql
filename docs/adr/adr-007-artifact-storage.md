# ADR-007: Artifact Storage for Execution Results

## Status

Accepted (implemented in `ArtifactStore` and executor services).

## Context

Query execution results need to be persisted for aggregation and downstream usage. Persisting raw results in memory would be expensive and non-durable for multi-step DAGs.

## Decision

Persist execution results as Parquet artifacts:

- Adapters return `ResultFrame` objects.
- `SqlExecutorService` writes results to an `ArtifactStore`.
- Aggregation reads artifacts and applies combine/post operations.

Backends are pluggable (`local`, `s3`, `adls`).

## Consequences

- Results are durable across pipeline stages.
- Aggregation operates on persisted artifacts, reducing memory pressure.
- Backends can be swapped without changing executor logic.

## Source references

- Artifact store base: `packages/core/src/nl2sql/execution/artifacts/base.py`
- Local store: `packages/core/src/nl2sql/execution/artifacts/local_store.py`
- Executor service: `packages/core/src/nl2sql/execution/executor/sql_executor.py`
