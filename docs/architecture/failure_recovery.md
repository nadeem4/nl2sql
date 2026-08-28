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
- Logical validation is the only validation gate; there is no physical/dry-run validation node in the graph.

### Execution
- SQL generation failures return `SQL_GEN_FAILED`.
- Executor returns errors for missing SQL, missing datasource, missing executor, or executor crashes.
- SQL execution failures return `EXECUTION_FAILED` based on adapter result.
- Execution is in-process on the pipeline thread pool. A driver-level crash is not contained and terminates the process; only Python-level exceptions become `PipelineError` values (see `../execution/isolation.md`).

### Storage
- Schema store uses SQLite or in-memory storage; failures manifest as exceptions on read/write (not caught in callers).
- Artifact store is invoked by the SQL executor; aggregation loads artifacts via `PolarsDuckdbEngine.load_scan()` using `ArtifactRef` URIs. A single store serves the local, S3, and ADLS backends; only local is verified against real storage (see `../storage/artifact-store.md`).
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
- `RESULT_ARTIFACT_PATH_TEMPLATE` renders the artifact path for every backend from the metadata the executor supplies (`tenant_id`, `request_id`, `schema_version`); an unfillable placeholder raises rather than writing a bad path (see `../storage/artifact-store.md`).
- With the default template, paths are `<backend root>/<tenant_id>/<request_id>.parquet`, so repeat execution with the same trace ID targets the same object.

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

- No dry-run or cost-estimate gate runs before execution, even where the adapter advertises `SUPPORTS_DRY_RUN` / `SUPPORTS_COST_ESTIMATE`.
- `VECTOR_BREAKER` is the only circuit breaker; LLM calls and SQL execution have no breaker and no fast-fail path.
- There is no process isolation around SQL execution, so a driver crash is unrecoverable at the engine level.
- Subgraph wrapper assumes executor output is present; earlier failures can cause wrapper-level errors.
- Pipeline completion does not imply success; `PipelineRunner` does not inspect `errors` and always returns `success=True` if the graph returns.
- No graph-level retries or replay; only subgraph local retries.

---

## Related Code

- `packages/nl2sql/src/nl2sql/pipeline/runtime.py`
- `packages/nl2sql/src/nl2sql/pipeline/graph.py`
- `packages/nl2sql/src/nl2sql/pipeline/routes.py`
- `packages/nl2sql/src/nl2sql/pipeline/graph_utils.py`
- `packages/nl2sql/src/nl2sql/pipeline/state.py`
- `packages/nl2sql/src/nl2sql/pipeline/subgraphs/sql_agent.py`
- `packages/nl2sql/src/nl2sql/common/errors.py`
- `packages/nl2sql/src/nl2sql/common/resilience.py`
- `packages/nl2sql/src/nl2sql/common/cancellation.py`
- `packages/nl2sql/src/nl2sql/pipeline/nodes/*/node.py`
- `packages/nl2sql/src/nl2sql/execution/executor/sql_executor.py`
- `packages/nl2sql/src/nl2sql/aggregation/aggregator.py`
- `packages/nl2sql/src/nl2sql/indexing/vector_store.py`
- `packages/nl2sql/src/nl2sql/schema/*.py`
