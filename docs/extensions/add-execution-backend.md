# Adding an Execution Backend

`SqlExecutorService` is the only execution backend. `ExecutorNode` constructs it
directly and gates it on the datasource declaring `supports_sql`.

## 1. Implement the executor service

Create a class that implements:

- `validate_request(request) -> list[PipelineError]`
- `execute(request) -> ExecutorResponse`

Use `ExecutorRequest`/`ExecutorResponse` as the contract.

## 2. Wire the executor

Construct the executor in `ExecutorNode.__init__` and select it in `__call__`
based on the adapter's capabilities. `ExecutorNode._supports_sql()` shows the
existing check: a datasource whose adapter does not declare the required
capability must get no executor rather than being run anyway.

## 3. Update routing (if needed)

If a new subgraph is required for the backend, add it as a node in:

- `packages/core/src/nl2sql/pipeline/graph.py`

and extend `resolve_subgraph()` in
`packages/core/src/nl2sql/pipeline/graph_utils.py` so the capabilities it
requires match the adapter's advertised capabilities.

## 4. Artifact handling

If the backend returns tabular results:

- Return a `ResultFrame`.
- Persist it via `ArtifactStore` to produce an `ArtifactRef`.

## Source references

- SQL executor: `packages/core/src/nl2sql/execution/executor/sql_executor.py`
- Executor node: `packages/core/src/nl2sql/pipeline/nodes/executor/node.py`
- Executor contracts: `packages/core/src/nl2sql/execution/contracts.py`
