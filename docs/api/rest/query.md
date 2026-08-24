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

### `SubQueryResponse`
| field | type | required | meaning |
| --- | --- | --- | --- |
| `id` | `str` | no | Sub-query identifier. |
| `intent` | `str` | no | Semantic intent of the sub-query. |
| `sql` | `str` | no | SQL generated for the sub-query. |
| `datasource_id` | `str` | no | Datasource the sub-query targets. |
| `schema_version` | `str` | no | Schema version used for planning. |

### `QueryResponse`

Mirrors `nl2sql.api.query_api.QueryResult` field for field.

| field | type | required | meaning |
| --- | --- | --- | --- |
| `sub_queries` | `List[SubQueryResponse]` | no | One entry per decomposed sub-query, each with its SQL. |
| `final_answer` | `Optional[Dict[str, Any]]` | no | Answer synthesizer payload (`summary`, `format_type`, `content`). |
| `errors` | `List[Dict[str, Any]]` | no | Pipeline errors (`node`, `message`, `error_code`, `severity`). |
| `trace_id` | `Optional[str]` | no | Trace identifier. |
| `reasoning` | `List[Dict[str, Any]]` | no | Reasoning events/logs. |
| `warnings` | `List[Dict[str, Any]]` | no | Warning events/logs. |
| `artifact_refs` | `Dict[str, Dict[str, Any]]` | no | Result artifact references keyed by execution node id. |

Result rows are not inlined in the response. They are written to artifact storage
and addressed through `artifact_refs` (`uri`, `format`, `row_count`, `columns`).

## Endpoints

### `POST /api/v1/query`

Source: `packages/api/src/nl2sql_api/routes/query.py`

Request model: `QueryRequest`

Response model: `QueryResponse`

Execution flow:
- Converts `user_context` to `UserContext` when present.
- Delegates to `engine.run_query(...)`, which returns a `QueryResult`.
- Maps the `QueryResult` field-for-field into `QueryResponse`.

The handler is declared with `def`, not `async def`: the pipeline performs blocking
LLM and database calls, so Starlette runs it in its threadpool instead of on the
event loop.

Errors:
- Pipeline failures are a normal `HTTP 200` carrying `errors`; they are not HTTP failures.
- Genuinely unexpected failures return `HTTP 500` with a generic detail; the
  traceback is logged server-side rather than returned to the client.
- An invalid request body returns `HTTP 422` (FastAPI validation).

Example response:

```json
{
  "sub_queries": [
    {
      "id": "sq-1",
      "intent": "top customers by revenue",
      "sql": "SELECT customer, SUM(revenue) FROM sales GROUP BY customer ORDER BY 2 DESC LIMIT 5",
      "datasource_id": "warehouse",
      "schema_version": "v3"
    }
  ],
  "final_answer": {
    "summary": "Top 5 customers by revenue",
    "format_type": "table",
    "content": "| customer | revenue |\n| --- | --- |"
  },
  "errors": [],
  "trace_id": "0f1c...",
  "reasoning": [],
  "warnings": [],
  "artifact_refs": {
    "sq-1": {
      "uri": "file:///artifacts/sq-1.parquet",
      "format": "parquet",
      "row_count": 5,
      "columns": ["customer", "revenue"]
    }
  }
}
```

## Tests

`packages/api/tests/test_query_routes.py` covers this endpoint with FastAPI's
`TestClient` and a stubbed engine (`get_engine` dependency override), so no
datasource, LLM or network access is required.
