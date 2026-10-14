# ExecutorNode

## Overview

- Delegates SQL execution to an executor service based on datasource capabilities.
- Persists results via the artifact store and returns `ExecutorResponse`.
- Sits after `GeneratorNode` in the SQL agent subgraph.
- Class: `ExecutorNode`
- Source: `packages/nl2sql/src/nl2sql/pipeline/nodes/executor/node.py`

---

## Responsibilities

- Validate required execution inputs (SQL and datasource ID).
- Gate execution on the datasource declaring `supports_sql`.
- Build and submit `ExecutorRequest`.
- Return executor response and propagate errors.

---

## Position in Execution Graph

Upstream:
- `GeneratorNode`

Downstream:
- Subgraph end (artifacts returned to control graph).

Trigger conditions:
- Executed when SQL generation succeeds.

```mermaid
flowchart LR
    Generator[GeneratorNode] --> Executor[ExecutorNode] --> EndNode[SubgraphEnd]
```

---

## Inputs

From `SubgraphExecutionState`:

- `generator_response.sql_draft` (required)
- `sub_query.datasource_id` (required)
- `sub_query.schema_version` (optional)
- `trace_id`, `subgraph_name`, `user_context`

From `Runtime`:

- `ds_registry`
- `tenant_id`

Validation performed:

- Missing SQL → `MISSING_SQL`
- Missing datasource ID → `MISSING_DATASOURCE_ID`
- No executor for datasource → `INVALID_STATE`

---

## Outputs

Mutations to `SubgraphExecutionState`:

- `executor_response` (`ExecutorResponse`)
- `errors` and `reasoning`

Side effects:

- Executes SQL against datasource via executor service.
- Reads the result a batch at a time when the adapter has `execute_batches` (`StreamingAdapterProtocol`), `RESULT_FETCH_BATCH_SIZE` rows per batch, and writes each batch to the artifact as it arrives (`ArtifactStore.write_batches`); an adapter without it is called through `execute` and its one frame is written the same way. A failure the adapter reports between batches fails the sub-query as a failed `execute` would, and nothing is written (see [artifact store](../../storage/artifact-store.md#batched-writes)). `metrics.row_count` and `metrics.bytes_returned` are summed over the batches.
- `SqlExecutorService` also takes `ExecutorRequest.parameters` and `max_rows`, which this node never sets: generated SQL carries no parameters and its `LIMIT` is in the text. The [curated matcher](curated_matcher_node.md) sets both for a verified query: the parameters reach the adapter in `AdapterRequest.parameters` and are bound, and the stream stops after `max_rows` rows, cutting the last batch and closing the adapter's stream.
- Persists results to artifact store, one artifact per sub-query: the request's `node_id` (the sub-query id) is passed to the store as `sub_query_id` and `dag_node_id`, so two sub-queries of one run never write the same file (see [artifact store](../../storage/artifact-store.md#one-artifact-per-sub-query)). The `ArtifactRef` records it as `sub_query_id`.

---

## Internal Flow (Step-by-Step)

1. Validate SQL and datasource ID.
2. Check the adapter declares `supports_sql`; otherwise return `INVALID_STATE`.
3. Build `ExecutorRequest` with trace/subgraph identifiers and schema version.
4. Execute request and return `ExecutorResponse`.
5. If the query succeeded with no rows, check the filter values (below).
6. On exception, emit `EXECUTOR_CRASH`.

### An empty result's filter values

"Which customers bought Metal?" planned as `Genre.Name = 'Metal'` returns no
rows because Chinook's genre is 'Heavy Metal'. Reported as is, the answer says
nobody bought any. So when a query succeeds with **zero rows**,
`value_check.check_values()` looks at every string literal the plan's `WHERE`
compares a bare column to with `=`, `IN` or `LIKE`, through `AND` and `OR`
only (a negated or `!=` comparison, a column inside a function, and a number
are left alone), at most five per sub-query:

1. **Does it exist?** `SELECT 1 FROM <table> WHERE <column> = '<value>' LIMIT 1`
   (`LIKE` for a pattern). If yes, nothing is said: the empty result stands.
2. **The closest values.** A case-insensitive match first
   (`LOWER(column) = LOWER('value')`, `DISTINCT`, `LIMIT 3`); failing that, a
   bounded `SELECT DISTINCT column ... LIMIT n` (n is 500, or the adapter's
   `row_limit` if lower) and the three most similar by `difflib` (cutoff 0.6).
3. **What it reports**, as `VALUE_NOT_FOUND`:
   - With a close value and a retry left, an `ERROR` telling the planner which
     values exist: "No Genre.Name value is 'Jaz', so the query returned no rows.
     The closest existing values are 'Jazz'; use one of them if it is what the
     question means." The SQL agent sends it back through the refiner (see
     [SQL agent](../subgraphs/sql_agent.md)). It is raised once per sub-query:
     an empty result after that retry is reported as a warning instead.
   - Otherwise a `WARNING`: "There is no Genre with Name 'Polka'." (with
     "; the closest is ..." when there is one). The sub-query still succeeds
     with its empty result, and the answer synthesizer is told the value does
     not exist (see [AnswerSynthesizerNode](answer_synthesizer_node.md)).

Every probe is a `SELECT` built with sqlglot and rendered by the adapter
(`render_sql`), so no dialect is named here; each has a `LIMIT` no higher than
the adapter's row cap. A table the role may not read (RBAC, as the logical
validator checks it) is never probed, a probe the database refuses claims
nothing, and a failure in the check never fails the run.

---

## Contracts & Interfaces

Implements a LangGraph node callable:

```
def __call__(self, state: SubgraphExecutionState) -> Dict[str, Any]
```

Key contracts:

- `ExecutorRequest`
- `ExecutorResponse`

---

## Determinism Guarantees

- Deterministic request construction.
- Execution results depend on external datasource state and are not deterministic.

---

## Error Handling

Emits `PipelineError` with:

- `MISSING_SQL`
- `MISSING_DATASOURCE_ID`
- `INVALID_STATE`
- `EXECUTION_FAILED`: the database rejected the query as wrong (the adapter's `retryable`); the SQL agent sends it back to the refiner while retries remain
- `DB_EXECUTION_ERROR`: the database refused or could not run the query (permission, timeout, connection); fatal
- `EXECUTOR_CRASH`: an exception escaped the adapter
- `VALUE_NOT_FOUND`: an empty result filtered on a value its column does not have; an `ERROR` (retried once) when a close value exists, a `WARNING` otherwise

---

## Retry + Idempotency

- No internal retry logic. The SQL agent's `check_execution` routes a retryable `EXECUTION_FAILED` back to the refiner (see [SQL agent](../subgraphs/sql_agent.md)).
- Idempotency depends on datasource and query semantics.

---

## Performance Characteristics

- External I/O dominates (database execution).
- Artifact persistence writes Parquet results.

---

## Observability

- Logger: `executor`
- Propagates executor response reasoning and errors.

---

## Configuration

- `tenant_id` from settings.
- Executor selection based on adapter capabilities.

---

## Extension Points

- Extend `SqlExecutorService` to alter execution behavior.
- Replace node in `build_sql_agent_graph()` to alter execution behavior.

---

## Known Limitations

- Executes in‑process on the pipeline thread pool; there is no process sandbox (see `../../execution/isolation.md`).
- No built‑in retries on transient execution failures.
- The filter-value check covers string literals in `WHERE` only, not `HAVING`, and reads at most 500 distinct values to find a similar one, so on a high-cardinality column a near miss can go unsuggested (the value is then reported as missing).

---

## Related Code

- `packages/nl2sql/src/nl2sql/pipeline/nodes/executor/node.py`
- `packages/nl2sql/src/nl2sql/execution/executor/sql_executor.py`
- `packages/nl2sql/src/nl2sql/pipeline/nodes/executor/value_check.py`
