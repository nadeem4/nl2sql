# Results API

The rows of a run, read after the fact. A run's `result_manifest` (in the [`/query`](query.md) response, and in the
stream's `result.manifest` event) names its saved results; these routes list them, page through them, download them and
draw them. `trace_id` is the run's `trace_id`; `result_id` is a `node_id` from the manifest.

Source: `packages/api/src/nl2sql_api/routes/query.py`

## Models

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


## Endpoints

### `GET /api/v1/results/{trace_id}`

The run's manifest: per result its `node_id`, `columns`, `row_count`, `truncated`, `column_kinds` and who asked
(`ResultManifest`). Never rows.

### `GET /api/v1/results/{trace_id}/{result_id}`

One page of a run's saved result.

| parameter | in | default | meaning |
| --- | --- | --- | --- |
| `trace_id` | path | | The run's `trace_id`. |
| `result_id` | path | | A `node_id` from the run's `result_manifest`. |
| `offset` | query | `0` | First row, from 0. Negative is `HTTP 422`. |
| `limit` | query | `100` | Rows per page, at least 1 (else `HTTP 422`). Above 1,000 it is cut to 1,000; `limit` in the response says which. |

Response model: `ResultPage`. Delegates to `engine.results.page(...)`, which
reads the rows lazily from the result's parquet artifact: only the slice asked for
is read. A page past the end has no rows.

```bash
curl -H "Authorization: Bearer $NL2SQL_KEY"   "http://localhost:8000/api/v1/results/0f1c.../combine_g1?offset=100&limit=100"
```

```json
{"request_id": "0f1c...", "result_id": "combine_g1", "columns": ["customer", "revenue"],
 "rows": [["acme", 42], ["globex", 40]], "offset": 100, "limit": 100,
 "total_rows": 102, "next_offset": null}
```

### `GET /api/v1/results/{trace_id}/{result_id}.csv`

The whole of a run's saved result as `text/csv` (UTF-8, header first), sent as an
attachment named `<trace_id>_<result_id>.csv`. Delegates to
`engine.results.csv(...)`, which reads the parquet artifact a slice at a time
and streams each slice out, so the result is never built in memory on the server.

## Errors and scope

- Both need a role, as `/query` does: no role is `HTTP 401`.
- A run is readable only by the caller who asked it: the request's caller (from
  the auth settings) is passed to `engine.results` as `user_context`, and must
  match the run's recorded asker. Results are read from the engine's own
  tenant folder (`TENANT_ID`, the `<tenant_id>` of the artifact path) and nowhere
  else. An unknown `trace_id`, another user's run, a run of another tenant, a
  `result_id` the run's manifest does not list, and an id that is not a plain
  token (letters, digits, `_` and `-`, so no `/`, `.` or `..`) are all
  `HTTP 404`, so a caller cannot tell one from the other or reach a path outside
  the run's folder.
- A caller with a `user_id` (an API key, or a proxy that sets the user header) reads only its own runs. A
  `static` caller has roles and no `user_id`, so its runs are anonymous: readable by any caller of the same tenant with the same
  roles, and by no caller with other roles: a `viewer` holding an `admin` run's trace id
  gets `HTTP 404`. See [Who can read a run](../core/results.md#who-can-read-a-run).
- The request body never names the caller: `user_context` is not a field of `POST /query`.


### `GET /api/v1/results/{trace_id}/{result_id}/chart`

The chart the saved result is drawn as, chosen from its column kinds by five fixed rules with no model call
(`engine.results.chart(...)`).

| parameter | in | default | meaning |
| --- | --- | --- | --- |
| `kind` | query | rules choose | Force `line`, `bar`, `big_number` or `table`. Anything else is a `422`; a kind the data cannot draw is a `422` naming the kinds it can. |
| `format` | query | `spec` | `spec` is the engine's `ChartSpec` (kind, encodings, the rows in `data`, a one-line `reason`, a `takeaway`). `vega-lite` is a Vega-Lite v5 document, rows inline and no colour set, ready to render. A table has no Vega-Lite form: `format=vega-lite` on one is a `422`. |

```bash
curl -H "Authorization: Bearer $NL2SQL_KEY"   "http://localhost:8000/api/v1/results/0f1c.../combine_g1/chart?format=vega-lite"
```

## Tests

`packages/api/tests/test_results_routes.py` covers these routes over the real facade and a local artifact store: the
manifest, paging, the cap, `HTTP 422` for a bad offset, limit, kind or format, the chart and its Vega-Lite form, the
complete CSV, `HTTP 404` for unknown, foreign and crafted ids and for an anonymous run read with other roles, and
`HTTP 401` without a role.
