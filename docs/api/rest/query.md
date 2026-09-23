# Query API

## Request/Response Models

Source: `packages/api/src/nl2sql_api/models/query.py`

### `QueryRequest`
| field | type | required | meaning |
| --- | --- | --- | --- |
| `natural_language` | `str` | yes | User query. |
| `datasource_id` | `Optional[str]` | no | Datasource override. |
| `execute` | `bool` | no | Execute SQL against datasource (default `true`). |
| `user_context` | `Optional[Dict[str, Any]]` | no | Ignored unless `NL2SQL_API_TRUST_BODY_ROLE=true` (local testing only); then an RBAC payload such as `{"roles": ["admin"]}`. See [Caller role](index.md#caller-role). |

### `SubQueryResponse`

Derives from `nl2sql.SubQueryResult`, so it has exactly the engine's fields.

| field | type | required | meaning |
| --- | --- | --- | --- |
| `id` | `str` | no | Sub-query identifier. |
| `intent` | `str` | no | Semantic intent of the sub-query. |
| `sql` | `str` | no | SQL generated for the sub-query. |
| `datasource_id` | `str` | no | Datasource the sub-query targets. |
| `schema_version` | `str` | no | Schema version used for planning. |
| `plan` | `Optional[Dict[str, Any]]` | no | The validated plan, dumped. |
| `validation` | `List[ValidationCheck]` | no | Validation checks (`name`, `passed`, `message`). |
| `rows` | `Optional[RowSample]` | no | Capped row sample (`columns`, `rows`, `total_rows`). |
| `status` | `str` | no | `"success"` or `"error"` for this sub-query, from its final attempt: `"success"` when it ended with SQL and, if executed, a result; otherwise `"error"`. A retry that recovers reports `"success"`. |
| `retry_count` | `int` | no | Plan/SQL refinement attempts made. |
| `plan_source` | `str` | no | `"cache"` when the plan came from the plan cache (no planner call; still validated and executed), otherwise `"llm"`. |

### `QueryResponse`

Derives from `nl2sql.QueryResult` (only `sub_queries` is narrowed to
`SubQueryResponse`), so the HTTP response cannot drift from what the engine returns.

| field | type | required | meaning |
| --- | --- | --- | --- |
| `sub_queries` | `List[SubQueryResponse]` | no | One entry per decomposed sub-query, each with its SQL. |
| `final_answer` | `Optional[Dict[str, Any]]` | no | Answer synthesizer payload (`summary`, `format_type`, `content`). |
| `errors` | `List[Dict[str, Any]]` | no | Pipeline errors (`node`, `message`, `error_code`, `severity`). |
| `trace_id` | `str` | no | Trace identifier. |
| `reasoning` | `List[Dict[str, Any]]` | no | Reasoning events/logs. |
| `warnings` | `List[Dict[str, Any]]` | no | Warning events/logs. |
| `artifact_refs` | `Dict[str, ArtifactRef]` | no | Result artifact references keyed by execution node id. |
| `status` | `str` | no | `"success"`, `"error"` or `"plan_only"` for the run. |
| `timings` | `Dict[str, float]` | no | Wall-clock seconds per graph node. |
| `usage` | `QuestionUsage` | no | LLM calls, input/cached/output/reasoning tokens and model time per node (`nodes`) and for the question (`total`), plus every call (`calls`) and the number of plans served from the plan cache (`plan_cache_hits`). The same model as `QueryResult.usage`; see [the core query API](../core/query.md#usage-tokens-calls-and-model-time). |
| `trace_path` | `Optional[str]` | no | Where the run's trace file was written on the server, or `null`. See [Debugging a Run](../../observability/debugging.md). |

Only a capped sample of the rows is inlined, in `sub_queries[].rows`. The full
result set is written to artifact storage and addressed through `artifact_refs`
(`uri`, `format`, `row_count`, `columns`).

## Endpoints

### `POST /api/v1/query`

Source: `packages/api/src/nl2sql_api/routes/query.py`

Request model: `QueryRequest`

Response model: `QueryResponse`

Execution flow:
- The `get_user_context` dependency supplies the caller's `UserContext` (see
  [Caller role](index.md#caller-role)); with no role it answers `HTTP 401`
  before the engine runs.
- Delegates to `engine.run_query(...)`, which returns a `QueryResult`.
- Returns that `QueryResult` as a `QueryResponse` (its subclass); there is no field mapping.

The handler is declared with `def`, not `async def`: the pipeline performs blocking
LLM and database calls, so Starlette runs it in its threadpool instead of on the
event loop.

Errors:
- Pipeline failures are a normal `HTTP 200` carrying `errors`; they are not HTTP failures.
- Genuinely unexpected failures return `HTTP 500` with a generic detail; the
  traceback is logged server-side rather than returned to the client.
- No role for the request returns `HTTP 401` with a message naming the settings.
- An invalid request body returns `HTTP 422` (FastAPI validation).

Example response:

```json
{
  "sub_queries": [
    {
      "id": "sq-1",
      "intent": "top customers by revenue",
      "sql": "SELECT s.customer, SUM(s.revenue) AS revenue FROM sales AS s GROUP BY s.customer ORDER BY SUM(s.revenue) DESC, s.customer LIMIT 5",
      "datasource_id": "warehouse",
      "schema_version": "v3",
      "plan": {"tables": [{"name": "sales", "alias": "s"}]},
      "validation": [
        {"name": "plan_present", "passed": true, "message": "Plan received from the planner"},
        {"name": "structure_and_schema", "passed": true, "message": "Tables, columns and joins resolve against the retrieved schema"},
        {"name": "policy", "passed": true, "message": "Role 'admin' may read every table in the plan"}
      ],
      "rows": {"columns": ["customer", "revenue"], "rows": [["acme", 42]], "total_rows": 5},
      "status": "success",
      "retry_count": 0,
      "plan_source": "llm"
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
  },
  "status": "success",
  "timings": {"ast_planner": 0.02, "logical_validator": 0.001, "generator": 0.006, "executor": 0.038},
  "usage": {
    "total": {"calls": 3, "input_tokens": 13200, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
              "output_tokens": 590, "reasoning_tokens": 0, "total_tokens": 13790, "latency_s": 4.1, "cost": null},
    "nodes": {
      "decomposer": {"calls": 1, "input_tokens": 1900, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
                     "output_tokens": 250, "reasoning_tokens": 0, "total_tokens": 2150, "latency_s": 1.3, "cost": null},
      "ast_planner": {"calls": 1, "input_tokens": 11000, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
                      "output_tokens": 300, "reasoning_tokens": 0, "total_tokens": 11300, "latency_s": 2.2, "cost": null},
      "answer_synthesizer": {"calls": 1, "input_tokens": 300, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
                             "output_tokens": 40, "reasoning_tokens": 0, "total_tokens": 340, "latency_s": 0.6, "cost": null}
    },
    "calls": [
      {"node": "decomposer", "model": "gpt-4o-2024-08-06", "input_tokens": 1900, "cached_input_tokens": 0,
       "cache_write_input_tokens": 0, "output_tokens": 250, "reasoning_tokens": 0, "total_tokens": 2150,
       "latency_s": 1.3, "cost": null, "usage_reported": true, "error": null}
    ],
    "plan_cache_hits": 0
  }
}
```

(Illustrative numbers. `usage.calls` is shortened to one entry; a real response lists all three.)

## Tests

`packages/api/tests/test_query_routes.py` and
`packages/api/tests/test_query_response_shape.py` cover this endpoint with FastAPI's
`TestClient` and a stubbed engine (`get_engine` dependency override) and a stubbed
caller (`get_user_context` override, role `admin`), so no datasource, LLM or
network access is required. `packages/api/tests/test_auth.py` covers the role
sources and the 401.
