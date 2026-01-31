# Execution Sandbox Architecture

NL2SQL includes a sandbox subsystem that provides **process-level isolation** for execution and indexing tasks. The sandbox is implemented via `ProcessPoolExecutor` pools managed by `SandboxManager`.

## Sandbox structure (available)

```mermaid
flowchart TD
    Caller[Execution/Indexing Task] --> Sandbox[get_execution_pool()/get_indexing_pool()]
    Sandbox --> Worker[ProcessPool Worker]
    Worker --> Adapter[DatasourceAdapterProtocol.execute()]
    Adapter --> Database[(Database)]
```

## Current wiring (as implemented)

- `SandboxManager` and `execute_in_sandbox()` exist and are production-ready.
- The default `SqlExecutorService` currently executes **in-process** and does not call `execute_in_sandbox()`.
- Circuit breakers are available in `nl2sql.common.resilience`, but `SqlExecutorService` does not wrap calls with `DB_BREAKER` today.

This means execution isolation is **available but not enforced** by default in SQL execution.

## Concurrency model

- `run_with_graph()` executes the control graph within a `ThreadPoolExecutor`.
- Sandbox pools are **process-based** and designed for isolation and crash containment.
- Execution and indexing pools are configured via `Settings.sandbox_exec_workers` and `Settings.sandbox_index_workers`.

## Sandbox APIs

- `SandboxManager.get_execution_pool()` for latency-sensitive execution.
- `SandboxManager.get_indexing_pool()` for throughput-heavy indexing.
- `execute_in_sandbox()` for timeouts and crash handling.

## Failure handling semantics

`execute_in_sandbox()` returns an `ExecutionResult` that captures:

- timeouts (worker hung)
- worker crashes (segfault/OOM)
- serialization or runtime errors

## Retry behavior

Retry behavior is not defined at the sandbox layer. Retries are controlled by:

- SQL agent retry loop (`sql_agent_max_retries`)
- Circuit breaker configuration (fail-fast)

## Source references

- Sandbox manager: `packages/core/src/nl2sql/common/sandbox.py`
- SQL executor: `packages/core/src/nl2sql/execution/executor/sql_executor.py`
