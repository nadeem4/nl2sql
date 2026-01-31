# ADR-005: Adapter Abstraction and Capability Routing

## Status

Accepted (implemented via `DatasourceAdapterProtocol`, registries, and routing).

## Context

NL2SQL must support heterogeneous datasources (SQL, REST, GraphQL, etc.) while keeping orchestration stable and deterministic.

## Decision

Adopt a **capability-driven adapter abstraction**:

- Adapters implement `DatasourceAdapterProtocol`.
- Capabilities are declared via `capabilities()`.
- Routing and execution select services/subgraphs based on capability subsets.

Adapters are discovered via Python entry points and registered at runtime based on configuration.

## Consequences

- New datasources can be integrated without changing core orchestration.
- Subgraphs and executors remain decoupled and capability-focused.
- Capability mismatches fail fast with clear errors.

## Source references

- Adapter protocol: `packages/adapter-sdk/src/nl2sql_adapter_sdk/protocols.py`
- Adapter discovery: `packages/core/src/nl2sql/datasources/discovery.py`
- Datasource registry: `packages/core/src/nl2sql/datasources/registry.py`
- Routing: `packages/core/src/nl2sql/pipeline/routes.py`
