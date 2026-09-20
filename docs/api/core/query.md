# Query API

## Purpose
Execute a natural language query using the NL2SQL pipeline.

## Responsibilities
- Invoke pipeline graph runtime.
- Return a structured `QueryResult`.

## Key Modules
- `packages/nl2sql/src/nl2sql/api/query_api.py`
- `packages/nl2sql/src/nl2sql/pipeline/runtime.py`
- `packages/nl2sql/src/nl2sql/pipeline/graph.py`
- `packages/nl2sql/src/nl2sql/pipeline/state.py`

## Public Surface

### QueryAPI.run_query

Source:
`packages/nl2sql/src/nl2sql/api/query_api.py`

Signature:
`run_query(natural_language: str, datasource_id: Optional[str] = None, execute: bool = True, user_context: Optional[UserContext] = None) -> QueryResult`

`run_query` passes the context's artifact store to `result_from_state`, so each
sub-query carries a capped row sample alongside its artifact reference.

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `natural_language` | `str` | yes | User query. |
| `datasource_id` | `Optional[str]` | no | Datasource override; otherwise resolved. |
| `execute` | `bool` | no | Whether to execute SQL against datasource. |
| `user_context` | `Optional[UserContext]` | no | RBAC context. |

Returns:
`QueryResult`, built from the pipeline graph state by `result_from_state`.

Raises:
No exceptions are raised by `run_query`; errors are returned in `QueryResult.errors`.

Side Effects:
- Pipeline execution, LLM calls, optional database execution.

Idempotency:
- Not guaranteed; execution can depend on external systems and time.

## Execution Lifecycle
- Create a per-run `CancellationToken` and install signal / Ctrl+X handlers bound to it.
- Build LangGraph pipeline from `build_graph`.
- Execute graph in thread pool with timeout.
- On timeout/cancel, return `PipelineError` with appropriate `ErrorCode`.

### QueryResult

Source:
`packages/nl2sql/src/nl2sql/api/query_api.py`

Fields:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `sub_queries` | `List[SubQueryResult]` | no | One entry per decomposed sub-query. |
| `final_answer` | `Optional[Dict[str, Any]]` | no | Answer synthesizer payload (`summary`, `format_type`, `content`). |
| `errors` | `List[Dict[str, Any]]` | no | Pipeline errors (`node`, `message`, `error_code`, `severity`). |
| `trace_id` | `str` | no | Trace identifier. |
| `reasoning` | `List[Dict[str, Any]]` | no | Reasoning events/logs. |
| `warnings` | `List[Dict[str, Any]]` | no | Warning events/logs. |
| `artifact_refs` | `Dict[str, ArtifactRef]` | no | Result artifact references keyed by execution node id. |
| `status` | `str` | no | `"success"`, `"error"`, `"plan_only"`, or `""` when nothing ran. |
| `timings` | `Dict[str, float]` | no | Wall-clock seconds per graph node, plus `LangGraph` for the whole run. |

`errors` holds only `ERROR` and `CRITICAL` entries; `WARNING`-severity pipeline
errors are appended to `warnings` as the same summary dict.

`status` is derived: `"error"` when any blocking error is present, otherwise
`"plan_only"` when no sub-query produced rows or an artifact (an `execute=False`
run), otherwise `"success"`. A state with no sub-queries and no errors leaves it
empty.

Only a capped sample of the rows is inlined, in `sub_queries[].rows`. The full
result set lives in artifact storage, addressed through `artifact_refs`.

### SubQueryResult

| name | type | required | meaning |
| --- | --- | --- | --- |
| `id` | `str` | no | Sub-query identifier. |
| `intent` | `str` | no | Semantic intent of the sub-query. |
| `sql` | `str` | no | SQL generated for the sub-query (`subgraph_outputs[<id>].sql_draft`). |
| `datasource_id` | `str` | no | Datasource the sub-query targets. |
| `schema_version` | `str` | no | Schema version used for planning. |
| `plan` | `Optional[Dict[str, Any]]` | no | The validated `PlanModel`, dumped. |
| `validation` | `List[ValidationCheck]` | no | `name`, `passed`, `message` per validation gate. |
| `rows` | `Optional[RowSample]` | no | Capped row sample; `None` when nothing executed or the artifact could not be read. |
| `status` | `str` | no | `"success"` or `"error"` for this sub-query. |
| `retry_count` | `int` | no | Plan/SQL refinement attempts made. |

### RowSample

| name | type | required | meaning |
| --- | --- | --- | --- |
| `columns` | `List[str]` | no | Column names, in result order. |
| `rows` | `List[List[Any]]` | no | At most `sample_rows` rows (default 50). |
| `total_rows` | `int` | no | Row count of the whole result, not of the sample. |

### State mapping

`result_from_state` normalises the raw graph state (LangGraph may return dicts or
model instances for nested values):

| `QueryResult` field | Graph state source |
| --- | --- |
| `sub_queries[].sql` | `subgraph_outputs[<id>].sql_draft` |
| `sub_queries[].id` / `.intent` / `.datasource_id` / `.schema_version` | `subgraph_outputs[<id>].sub_query` |
| `sub_queries[].plan` / `.validation` / `.status` / `.retry_count` | `subgraph_outputs[<id>]` |
| `sub_queries[].rows` | `artifact_store.read_result_frame(subgraph_outputs[<id>].artifact)` |
| `final_answer` | `answer_synthesizer_response.final_answer` |
| `errors`, `reasoning`, `warnings`, `trace_id`, `artifact_refs`, `timings` | top-level state |

`errors` are projected onto a client-safe summary; `stack_trace` and `details` are
deliberately not exposed.

`result_from_state(state, artifact_store=None, sample_rows=50)` reads rows only
when an artifact store is supplied. A failed read is not fatal: `rows` stays
`None` and a warning is appended.

`timings` is collected by `NodeTimingCallback`
(`packages/nl2sql/src/nl2sql/pipeline/timing.py`), a LangChain callback handler
that `run_with_graph` always attaches. It keys on `metadata["langgraph_node"]`
where LangGraph provides it, so the keys are node names such as `ast_planner`,
`logical_validator`, `generator` and `executor`.
