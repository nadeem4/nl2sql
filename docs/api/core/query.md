# Query API

## Purpose
Execute a natural language query using the NL2SQL pipeline.

## Responsibilities
- Invoke pipeline graph runtime.
- Return a structured `QueryResult`.

## Key Modules
- `packages/core/src/nl2sql/api/query_api.py`
- `packages/core/src/nl2sql/pipeline/runtime.py`
- `packages/core/src/nl2sql/pipeline/graph.py`
- `packages/core/src/nl2sql/pipeline/state.py`

## Public Surface

### QueryAPI.run_query

Source:
`packages/core/src/nl2sql/api/query_api.py`

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
`QueryResult` with fields: `sql`, `results`, `final_answer`, `errors`, `warnings`, `trace_id`, `reasoning`.

Raises:
No exceptions are raised by `run_query`; errors are returned in `QueryResult.errors`.

Side Effects:
- Pipeline execution, LLM calls, optional database execution.

Idempotency:
- Not guaranteed; execution can depend on external systems and time.

## Execution Lifecycle
- Initialize cancellation and signal handlers.
- Build LangGraph pipeline from `build_graph`.
- Execute graph in thread pool with timeout.
- On timeout/cancel, return `PipelineError` with appropriate `ErrorCode`.

### QueryResult

Source:
`packages/core/src/nl2sql/api/query_api.py`

Fields:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `sql` | `Optional[str]` | no | Generated SQL (if any). |
| `results` | `list` | no | Result rows (adapter-dependent). |
| `final_answer` | `Optional[str]` | no | Natural language answer. |
| `errors` | `list` | no | Pipeline errors. |
| `trace_id` | `Optional[str]` | no | Trace identifier. |
| `reasoning` | `List[dict]` | no | Reasoning events/logs. |
| `warnings` | `List[dict]` | no | Warning events/logs. |
