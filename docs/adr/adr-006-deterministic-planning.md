# ADR-006: Deterministic Planning and Stable IDs

## Status

Accepted (implemented in decomposer and DAG models).

## Context

Enterprise workflows require repeatable orchestration to enable reliable caching, debugging, and audit trails. Non-deterministic planning introduces unstable IDs and inconsistent execution paths.

## Decision

Use **stable hashes and deterministic layering**:

- `DecomposerNode` generates stable sub-query and post-op IDs by hashing content.
- `ExecutionDAG._layered_toposort()` deterministically computes execution layers.
- Aggregation processes layers in deterministic order.

## Consequences

- Artifact keys and execution node IDs are stable across runs.
- Deterministic routing and aggregation simplify debugging and auditing.
- Planning remains reproducible even when subgraphs run in parallel.

## Source references

- Decomposer: `packages/core/src/nl2sql/pipeline/nodes/decomposer/node.py`
- Execution DAG: `packages/core/src/nl2sql/pipeline/nodes/global_planner/schemas.py`
- Aggregation: `packages/core/src/nl2sql/aggregation/aggregator.py`
