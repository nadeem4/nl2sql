# Adding an Execution Backend

Execution backends are implemented as `ExecutorService` instances and registered in `ExecutorRegistry`.

## 1. Implement the executor service

Create a class that implements:

- `validate_request(request) -> list[PipelineError]`
- `execute(request) -> ExecutorResponse`

Use `ExecutorRequest`/`ExecutorResponse` as the contract.

## 2. Register the executor

Register the executor in `ExecutorRegistry` for a capability key. The registry will select executors based on datasource capabilities.

## 3. Update routing (if needed)

If a new subgraph is required for the backend, add a subgraph spec in:

- `packages/core/src/nl2sql/pipeline/subgraphs/registry.py`

Ensure its `required_capabilities` match the adapter’s advertised capabilities.

## 4. Artifact handling

If the backend returns tabular results:

- Return a `ResultFrame`.
- Persist it via `ArtifactStore` to produce an `ArtifactRef`.

## Source references

- Executor service base: `packages/core/src/nl2sql/execution/executor/base.py`
- Executor registry: `packages/core/src/nl2sql/execution/executor/registry.py`
- Executor contracts: `packages/core/src/nl2sql/execution/contracts.py`
