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

Result rows are not inlined. They live in artifact storage and are addressed
through `artifact_refs`.

### SubQueryResult

| name | type | required | meaning |
| --- | --- | --- | --- |
| `id` | `str` | no | Sub-query identifier. |
| `intent` | `str` | no | Semantic intent of the sub-query. |
| `sql` | `str` | no | SQL generated for the sub-query (`subgraph_outputs[<id>].sql_draft`). |
| `datasource_id` | `str` | no | Datasource the sub-query targets. |
| `schema_version` | `str` | no | Schema version used for planning. |

### State mapping

`result_from_state` normalises the raw graph state (LangGraph may return dicts or
model instances for nested values):

| `QueryResult` field | Graph state source |
| --- | --- |
| `sub_queries[].sql` | `subgraph_outputs[<id>].sql_draft` |
| `sub_queries[].id` / `.intent` / `.datasource_id` / `.schema_version` | `subgraph_outputs[<id>].sub_query` |
| `final_answer` | `answer_synthesizer_response.final_answer` |
| `errors`, `reasoning`, `warnings`, `trace_id`, `artifact_refs` | top-level state |

`errors` are projected onto a client-safe summary; `stack_trace` and `details` are
deliberately not exposed.
