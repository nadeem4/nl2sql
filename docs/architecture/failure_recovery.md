# Failure + Recovery Architecture

## Overview
Failure in this system is represented as structured `PipelineError` objects accumulated in graph state, explicit exceptions that short-circuit a node/subgraph, or runtime timeouts/cancellations handled by the orchestrator. Most node failures are captured and returned in state rather than raising, so the graph can continue unless routing logic or wrappers explicitly stop or crash. Recovery is limited to a local retry loop inside the SQL agent subgraph; there is no graph-level replay or global retry.

---

## Failure Domains

### Input
- Missing or invalid configuration raises during context construction (e.g., missing vector store path or collection name), preventing pipeline startup.
- Explicit datasource overrides fail if the datasource is unknown or not allowed, returning `SECURITY_VIOLATION` or `INVALID_STATE`.
- Missing LLMs for specific nodes (e.g., refiner) return `MISSING_LLM` errors.

### Retrieval
- Vector store calls are wrapped by `VECTOR_BREAKER`; breaker open or retrieval errors propagate into resolver or schema retriever.
- Resolver returns `SCHEMA_RETRIEVAL_FAILED` if no candidate datasources are found.
- Schema retriever falls back to full schema snapshot if vector retrieval yields no tables; if schema store returns `None`, it silently returns an empty table list.

### Planning
- Decomposer LLM failures return `ORCHESTRATOR_CRASH` (critical) and empty responses.
- AST planner LLM failures return `PLANNING_FAILURE` and a `None` plan.
- Global planner failures return `PLANNER_FAILED` and `execution_dag=None`.

### Validation
- Logical validation returns structured errors for missing tables, columns, invalid plan structure, or security violations.
- Physical validation is implemented but not wired into the SQL agent subgraph flow and does not execute in the current graph.

### Execution
- SQL generation failures return `SQL_GEN_FAILED`.
- Executor returns errors for missing SQL, missing datasource, missing executor, or executor crashes.
- SQL execution failures return `EXECUTION_FAILED` based on adapter result.
- Sandbox execution errors (timeouts, worker crashes) are surfaced only when the physical validator uses the sandbox.

### Storage
- Schema store uses SQLite or in-memory storage; failures manifest as exceptions on read/write (not caught in callers).
- Artifact store is invoked by the SQL executor; aggregation loads artifacts via `AggregationEngine.load_scan()` using `ArtifactRef` URIs. Local, S3, and ADLS backends exist in this repository (see `../storage/artifact-store.md` for implementation details and limitations).
- Result store and execution store are in-memory only and not used for recovery.

---

## Node-Level Failures

### How nodes fail
- Most nodes use try/except and return a `PipelineError` (with severity + error code) in their response.
- Some routing logic raises `PipelineError` directly (e.g., no compatible subgraph found).
- Nodes often return partial state plus errors (e.g., resolver returns a response plus errors).

### Propagation
- `GraphState.errors` and `SubgraphExecutionState.errors` accumulate errors via list reducers.
- Downstream nodes generally do not halt unless routing logic explicitly stops (SQL agent checks) or a wrapper crashes.

### Local handling
- SQL agent uses retry routing based on `PipelineError.is_retryable`.
- Other nodes do not retry; they return errors and allow the graph to proceed unless missing required inputs causes downstream failures.

---

## Subgraph Failures

### Containment
- Each subgraph runs with its own `SubgraphExecutionState`. Errors inside the subgraph are merged back into the main graph state.

### Abort vs continue
- SQL agent subgraph routes to `END` when:
  - The planner fails to produce a plan and errors are non-retryable.
  - Logical validation returns non-retryable errors.
  - Retry count reaches `sql_agent_max_retries`.
  - Cancellation is detected.
- If errors are retryable and retry budget remains, the subgraph loops through `retry_handler -> refiner -> planner`.

### Partial recovery
- Recovery is limited to re-planning and refining within the SQL agent. There is no partial recovery at the aggregator or graph layer.
- Subgraph wrapper assumes an `executor_response` is present; if executor output is missing (e.g., early failure), wrapper-level failures are possible.

---

## Graph-Level Failures

### Request termination
- `run_with_graph` terminates early on cancellation (`CANCELLED`) or global timeout (`PIPELINE_TIMEOUT`).
- Unhandled exceptions in graph execution return `UNKNOWN_ERROR` with stack trace.

### Cleanup
- Signal handlers are restored after execution. There is no explicit cleanup of artifacts or partial state.

### User-facing errors
- Errors are returned in the final state. `PipelineRunner` returns `success=True` on graph completion regardless of errors in state.

---

## Retry Architecture

### Retry scope
- Only the SQL agent subgraph retries (planner and validation loop).
- Other nodes (resolver, decomposer, global planner, generator, executor, aggregator, answer synthesizer) do not retry.

### Backoff
- Exponential backoff with jitter in `retry_handler` using:
  - `SQL_AGENT_RETRY_BASE_DELAY_SEC`
  - `SQL_AGENT_RETRY_MAX_DELAY_SEC`
  - `SQL_AGENT_RETRY_JITTER_SEC`

### Idempotency
- Subquery IDs are stable hashes, so retries regenerate the same subquery IDs.
- Executor writes artifacts keyed by subquery IDs; overwrite semantics depend on the artifact store implementation (not present in this repo).

---

## Artifact Consistency

### Partial writes
- Executor returns an `ArtifactRef` only on successful adapter execution.
- Aggregator expects an artifact for each scan node; missing artifacts raise and are surfaced as `AGGREGATOR_FAILED`.

### Overwrites
- For S3/ADLS backends, `RESULT_ARTIFACT_PATH_TEMPLATE` is intended to render paths with `tenant_id`, `request_id`, `subgraph_name`, `dag_node_id`, and `schema_version`, but path rendering is not implemented in this repo (see `../storage/artifact-store.md`).
- For the local backend, paths are `<result_artifact_base_uri>/<tenant_id>/<request_id>.parquet`, so repeat execution with the same trace ID targets the same file.

### Cleanup
- No cleanup or rollback is implemented for artifacts or partial aggregation results.

---

## Recovery Paths

### What can be retried
- SQL agent planner/validation loop for retryable errors (non-fatal error codes and non-critical severity).

### What must restart
- Any graph-level failure (timeout, cancellation, unknown exception) requires a new run.
- Resolver failures, decomposer failures, global planner failures, generator failures, executor failures, and aggregator failures have no local recovery and require a new run.

### What is unrecoverable
- `FATAL_ERRORS` or `CRITICAL` severity errors (security violations, missing datasource ID, missing LLM, invalid state) terminate the subgraph or graph without retry.

---

## Replay Support

Replay is not supported. There is no persisted graph state or execution log to re-run nodes; state is kept in memory and discarded after completion. Artifact references are stored in state only and are not used for graph replay.

---

## Known Gaps

- Physical validation node exists but is not wired into the SQL agent subgraph, so dry-run and cost checks do not execute.
- LLM circuit breaker is defined but never applied to LLM calls.
- Database circuit breaker only guards physical validation; SQL execution is not wrapped and does not use the sandbox.
- Subgraph wrapper assumes executor output is present; earlier failures can cause wrapper-level errors.
- Pipeline completion does not imply success; `PipelineRunner` does not inspect `errors` and always returns `success=True` if the graph returns.
- No graph-level retries or replay; only subgraph local retries.

---

## Related Code

- `packages/core/src/nl2sql/pipeline/runtime.py`
- `packages/core/src/nl2sql/pipeline/graph.py`
- `packages/core/src/nl2sql/pipeline/routes.py`
- `packages/core/src/nl2sql/pipeline/graph_utils.py`
- `packages/core/src/nl2sql/pipeline/state.py`
- `packages/core/src/nl2sql/pipeline/subgraphs/sql_agent.py`
- `packages/core/src/nl2sql/common/errors.py`
- `packages/core/src/nl2sql/common/resilience.py`
- `packages/core/src/nl2sql/common/sandbox.py`
- `packages/core/src/nl2sql/pipeline/nodes/*/node.py`
- `packages/core/src/nl2sql/execution/executor/sql_executor.py`
- `packages/core/src/nl2sql/aggregation/aggregator.py`
- `packages/core/src/nl2sql/indexing/vector_store.py`
- `packages/core/src/nl2sql/schema/*.py`
