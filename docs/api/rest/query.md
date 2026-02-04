# Query API

## Request/Response Models

Source: `packages/api/src/nl2sql_api/models/query.py`

### `QueryRequest`
| field | type | required | meaning |
| --- | --- | --- | --- |
| `natural_language` | `str` | yes | User query. |
| `datasource_id` | `Optional[str]` | no | Datasource override. |
| `execute` | `bool` | no | Execute SQL against datasource (default `true`). |
| `user_context` | `Optional[Dict[str, Any]]` | no | RBAC context payload. |

### `QueryResponse`
| field | type | required | meaning |
| --- | --- | --- | --- |
| `sql` | `Optional[str]` | no | Generated SQL. |
| `results` | `list` | no | Query results. |
| `final_answer` | `Optional[str]` | no | Natural language answer. |
| `errors` | `list` | no | Pipeline errors. |
| `trace_id` | `Optional[str]` | no | Trace identifier. |
| `reasoning` | `List[Dict[str, Any]]` | no | Reasoning events/logs. |
| `warnings` | `List[Dict[str, Any]]` | no | Warning events/logs. |

## Endpoints

### `POST /api/v1/query`

Source: `packages/api/src/nl2sql_api/routes/query.py`

Request model: `QueryRequest`

Response model: `QueryResponse`

Execution flow:
- Converts `user_context` to `UserContext` when present.
- Delegates to `engine.run_query(...)`.
- Maps the `QueryResult` into `QueryResponse`.

Errors:
- Unhandled exceptions return `HTTP 500` with `detail=str(e)`.

### `GET /api/v1/query/{trace_id}`

Source: `packages/api/src/nl2sql_api/routes/query.py`

Response model: `QueryResponse`

Execution flow:
- Delegates to `QueryService.get_result(...)`.

Notes:
- `QueryService.get_result` is not implemented in current codebase. Calls will fail
  unless implemented.
