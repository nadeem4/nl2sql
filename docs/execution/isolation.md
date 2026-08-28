# Execution Isolation and Concurrency

This page describes how a pipeline run is bounded, cancelled, and protected in
the current implementation. It is deliberately explicit about what the engine
does **not** do, because the boundaries matter when you reason about failure.

## There is no process sandbox

The engine runs entirely **in one process**. `run_with_graph()` dispatches the
graph onto a `concurrent.futures.ThreadPoolExecutor`
([`pipeline/runtime.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/runtime.py)),
sized by `settings.sandbox_exec_workers` (`SANDBOX_EXEC_WORKERS`, default `4`).
Threads share one interpreter and one address space.

Two consequences follow, and neither is a sandbox property:

- A hard crash in a database driver (segfault, `abort()`) terminates the whole
  process, including the orchestrator. There is no disposable worker to absorb
  it.
- Memory is shared. A run cannot be isolated from another run by the runtime.

The setting is named `sandbox_exec_workers` for historical reasons. It controls
**thread-pool width**, not isolation.

## What the engine actually guarantees

### Validation before SQL exists

This is the engine's real safety property, and it holds. The LLM emits an
**Abstract Syntax Tree**, never SQL text. The AST passes through
`LogicalValidatorNode`
([`pipeline/nodes/validator/node.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/pipeline/nodes/validator/node.py))
before the generator is reached. That node:

- resolves every column against the retrieved schema snapshot using
  `sqlglot.optimizer.qualify`;
- enforces RBAC table policy via `RBAC.get_allowed_tables()`
  ([`auth/rbac.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/auth/rbac.py)),
  failing closed when no datasource ID is present.

An unvalidated plan never becomes SQL, so it never reaches an adapter. This is
a *correctness and authorization* gate, not a containment boundary: it prevents
bad SQL from being generated, it does not contain a driver that misbehaves on
SQL that passed.

### Per-run cancellation

Each invocation constructs its own `CancellationToken`
([`common/cancellation.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/common/cancellation.py))
and threads it to nodes through
`config={"configurable": {"cancellation_token": token}}`. Because the token is
per-run, cancelling one run never affects another.

Nodes observe the token and unwind cooperatively, so a cancelled run returns a
normal result carrying a `CANCELLED` `PipelineError` rather than raising.
`run_with_graph()` cancels the token on `SIGINT`/`SIGTERM`, and on Windows
interactive terminals also on Ctrl+X.

Cancellation is **cooperative**: a node blocked inside a driver call does not
abandon that call. The token is checked between and within nodes, not inside
third-party I/O.

### Global timeout

`settings.global_timeout_sec` (`GLOBAL_TIMEOUT_SEC`, default `60`) is enforced by
`future.result(timeout=...)` in `run_with_graph()`. On expiry the caller gets a
`PIPELINE_TIMEOUT` error and a user-facing message.

The timeout bounds **how long the caller waits**, not how long the work runs.
Since the executor is a thread pool, the underlying thread is not killed and may
continue until its own I/O completes.

### One circuit breaker

`VECTOR_BREAKER`
([`common/resilience.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/common/resilience.py))
is the only breaker the system defines (`fail_max=5`, `reset_timeout=30`). It
wraps vector-store retrieval in
[`indexing/vector_store.py`](https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/indexing/vector_store.py)
only.

LLM calls and SQL execution are **not** breaker-guarded. Their failures surface
as `PipelineError` values in graph state; see
[Error Handling](../observability/error-handling.md).

### Adapter-level limits

Adapters enforce `row_limit` and `max_bytes` on results
(see [SDK Reference](../adapters/sdk.md)). These cap payload size; they are not
isolation.

## Summary

| Mechanism | Module | What it bounds |
| :--- | :--- | :--- |
| AST validation + RBAC | `pipeline/nodes/validator/node.py` | What SQL may be generated at all |
| `CancellationToken` | `common/cancellation.py` | One run, cooperatively |
| `global_timeout_sec` | `pipeline/runtime.py` | How long the caller waits |
| `VECTOR_BREAKER` | `common/resilience.py` | Vector retrieval only |
| `ThreadPoolExecutor` | `pipeline/runtime.py` | Concurrency, **not** isolation |

If you need blast-radius containment against driver-level crashes, run the
engine in a process you are willing to lose (a per-request worker, a container
replica) and rely on your supervisor to restart it. The engine does not provide
that boundary itself.
