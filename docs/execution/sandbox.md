# Execution Sandbox Architecture

Execution and indexing run in isolated `ProcessPoolExecutor` pools managed by `SandboxManager`. This isolates driver crashes and heavy I/O from the main orchestration process.

## Sandbox structure

```mermaid
flowchart TD
    ExecNode[ExecutorNode] --> Breaker[DB_BREAKER]
    Breaker --> Sandbox[get_execution_pool()]
    Sandbox --> Worker[ProcessPool Worker]
    Worker --> Adapter[DatasourceAdapterProtocol.execute()]
    Adapter --> Database[(Database)]
```

## Sandbox API

- `SandboxManager.get_execution_pool()` for latency-sensitive execution.
- `SandboxManager.get_indexing_pool()` for schema indexing and ingestion.
- `execute_in_sandbox()` for structured timeout and crash handling.

## Failure handling

`execute_in_sandbox()` returns an `ExecutionResult` with error details on:

- Timeouts (`FutureTimeout`)
- Broken worker processes (`BrokenProcessPool`)
- Generic exceptions and serialization errors

## Source references

- Sandbox manager: `packages/core/src/nl2sql/common/sandbox.py`
- Executor service: `packages/core/src/nl2sql/execution/executor/sql_executor.py`
