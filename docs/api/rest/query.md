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
| `row_cap` | `Optional[int]` | no | The row limit that bounded this sub-query's SQL, or `null` when the plan's own `LIMIT` was smaller. |
| `truncated` | `bool` | no | True when the result reached `row_cap`, so rows may be missing. |

### `QueryResponse`

Derives from `nl2sql.QueryResult` (only `sub_queries` is narrowed to
`SubQueryResponse`), so the HTTP response cannot drift from what the engine returns.

| field | type | required | meaning |
| --- | --- | --- | --- |
| `sub_queries` | `List[SubQueryResponse]` | no | One entry per decomposed sub-query, each with its SQL. |
| `final_answer` | `Optional[Dict[str, Any]]` | no | Answer synthesizer payload (`summary`, `format_type`, `content`, `warnings`, `truncated`). |
| `errors` | `List[Dict[str, Any]]` | no | Pipeline errors (`node`, `message`, `error_code`, `severity`, `provider`, `provider_response`). `provider` and `provider_response` are set for a `PROVIDER_*` code and `null` otherwise; see [provider failures](../../observability/error-handling.md#provider-failures). |
| `trace_id` | `str` | no | Trace identifier. |
| `reasoning` | `List[Dict[str, Any]]` | no | Reasoning events/logs. |
| `warnings` | `List[Dict[str, Any]]` | no | Warning events/logs. |
| `artifact_refs` | `Dict[str, ArtifactRef]` | no | Result artifact references keyed by execution node id. |
| `status` | `str` | no | `"success"`, `"error"` or `"plan_only"` for the run. |
| `timings` | `Dict[str, float]` | no | Wall-clock seconds per graph node. |
| `usage` | `QuestionUsage` | no | LLM calls, input/cached/output/reasoning tokens and model time per node (`nodes`) and for the question (`total`), plus every call (`calls`) and the number of plans served from the plan cache (`plan_cache_hits`). The same model as `QueryResult.usage`; see [the core query API](../core/query.md#usage-tokens-calls-and-model-time). |
| `trace_path` | `Optional[str]` | no | Where the run's trace file was written on the server, or `null`. See [Debugging a Run](../../observability/debugging.md). |
| `truncated` | `bool` | no | True when any sub-query's result was cut at its row limit. |
| `result_manifest` | `List[ResultSummary]` | no | The run's saved results, one per terminal DAG node: `node_id`, `columns`, `row_count` (the whole result's) and `truncated` (a sub-query feeding it was cut at its row limit). Never the rows: read those with the two result routes below, `result_id` being the `node_id`. Empty when nothing was saved (`execute: false`, or the run failed before aggregation). |

Only a capped sample of the rows is inlined, in `sub_queries[].rows`. The full
result set is written to artifact storage and addressed through `artifact_refs`
(`uri`, `format`, `row_count`, `columns`). The run's combined results are listed in
`result_manifest` and read back, a page at a time or as CSV, with
[`GET /api/v1/query/{request_id}/results/{result_id}`](#get-apiv1queryrequest_idresultsresult_id)
and [`.../{result_id}.csv`](#get-apiv1queryrequest_idresultsresult_idcsv).

### `ResultPage`

| field | type | meaning |
| --- | --- | --- |
| `request_id` | `str` | The run's `trace_id`. |
| `result_id` | `str` | The result's `node_id` from `result_manifest`. |
| `columns` | `List[str]` | Column names, in row order. |
| `rows` | `List[List[Any]]` | `rows[i]` is row `offset + i` of the result. |
| `offset` | `int` | The first row returned, from 0. |
| `limit` | `int` | The page size used, after the cap of 1,000. |
| `total_rows` | `int` | Rows in the whole result. |
| `next_offset` | `Optional[int]` | Where the next page starts, or `null` when this page reaches the end. |

An `errors[]` entry for a key the provider rejected:

```json
{
  "node": "datasource_resolver",
  "message": "OpenAI rejected the API key.",
  "error_code": "PROVIDER_AUTH_FAILED",
  "severity": "ERROR",
  "provider": "OpenAI",
  "provider_response": "HTTP 401 (invalid_api_key): Incorrect API key provided: [redacted key]. You can find your API key at https://platform.openai.com/account/api-keys."
}
```

Branch on `error_code`, show `message`, and keep `provider_response` for a "provider
response" disclosure. The codes are listed under
[provider failures](../../observability/error-handling.md#provider-failures).

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
      "plan_source": "llm",
      "row_cap": null,
      "truncated": false
    }
  ],
  "final_answer": {
    "summary": "Acme leads the top 5 customers by revenue, with 42.",
    "format_type": "text",
    "content": "Acme leads with 42. The full list of 5 customers is in the table.",
    "warnings": [],
    "truncated": false
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

### `GET /api/v1/query/{request_id}/results/{result_id}`

One page of a run's saved result.

| parameter | in | default | meaning |
| --- | --- | --- | --- |
| `request_id` | path | | The run's `trace_id`. |
| `result_id` | path | | A `node_id` from the run's `result_manifest`. |
| `offset` | query | `0` | First row, from 0. Negative is `HTTP 422`. |
| `limit` | query | `100` | Rows per page, at least 1 (else `HTTP 422`). Above 1,000 it is cut to 1,000; `limit` in the response says which. |

Response model: `ResultPage`. Delegates to `engine.get_result_page(...)`, which
reads the rows lazily from the result's parquet artifact: only the slice asked for
is read. A page past the end has no rows.

```bash
curl -H "X-NL2SQL-Role: admin"   "http://localhost:8000/api/v1/query/0f1c.../results/combine_g1?offset=100&limit=100"
```

```json
{"request_id": "0f1c...", "result_id": "combine_g1", "columns": ["customer", "revenue"],
 "rows": [["acme", 42], ["globex", 40]], "offset": 100, "limit": 100,
 "total_rows": 102, "next_offset": null}
```

### `GET /api/v1/query/{request_id}/results/{result_id}.csv`

The whole of a run's saved result as `text/csv` (UTF-8, header first), sent as an
attachment named `<request_id>_<result_id>.csv`. Delegates to
`engine.export_result_csv(...)`, which reads the parquet artifact a slice at a time
and streams each slice out, so the result is never built in memory on the server.

### Errors and scope of the result routes

- Both need a role, as `/query` does: no role is `HTTP 401`.
- Results are read from the engine's own tenant folder (`TENANT_ID`, the
  `<tenant_id>` of the artifact path) and nowhere else. An unknown `request_id`, a
  run of another tenant, a `result_id` the run's manifest does not list, and an id
  that is not a plain token (letters, digits, `_` and `-`, so no `/`, `.` or `..`)
  are all `HTTP 404`, so a caller cannot tell one from the other or reach a path
  outside the run's folder.
- There is no access control beyond that yet: any caller with a role who knows a
  run's `trace_id` can read its result.

## Tests

`packages/api/tests/test_query_results_routes.py` covers the result routes over the
real facade and a local artifact store: paging, the cap, `HTTP 422` for a bad
offset or limit, the complete CSV, `HTTP 404` for unknown, foreign and crafted ids,
and `HTTP 401` without a role.

`packages/api/tests/test_query_routes.py` and
`packages/api/tests/test_query_response_shape.py` cover this endpoint with FastAPI's
`TestClient` and a stubbed engine (`get_engine` dependency override) and a stubbed
caller (`get_user_context` override, role `admin`), so no datasource, LLM or
network access is required. `packages/api/tests/test_auth.py` covers the role
sources and the 401.
