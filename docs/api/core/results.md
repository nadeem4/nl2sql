# Results API

## Purpose
`engine.results`: the results a run saved, read back by its `trace_id`, and the chart their data is drawn as.

## Key Modules
- `packages/nl2sql/src/nl2sql/api/results_api.py` (`ResultsAPI`, internal)
- `packages/nl2sql/src/nl2sql/execution/artifacts/results.py` (`ResultManifest`, `ResultPage`, `ResultReader`)
- `packages/nl2sql/src/nl2sql/charts/spec.py` (`ChartSpec`)

## Public Surface

| Method | What it does |
|---|---|
| `manifest(run_id, tenant_id=None) -> ResultManifest` | Which results the run saved: per result its node id, columns, row count and truncated flag. Never the rows. |
| `page(run_id, result_id, offset=0, limit=100, tenant_id=None) -> ResultPage` | Rows `offset` to `offset + limit` of one saved result, read lazily from its parquet file. |
| `csv(run_id, result_id, tenant_id=None) -> Iterator[bytes]` | The whole result as UTF-8 CSV, a slice at a time. |
| `chart(result, result_id=None, *, kind=None, tenant_id=None) -> ChartSpec` | The chart the result is drawn as ([Charts](../../charts/index.md)): a `ChartSpec` chosen by rules from the saved result's column kinds, with no model call. `result` is a `QueryResult` (a single sub-query's plan refines the kinds, and its intent titles the chart) or a `trace_id`. `result_id` may be left out when the run saved one result. `kind` forces `line`, `bar`, `big_number` or `table`; one the data cannot draw is a `ValueError` naming those it can. Raises `ResultNotFound` as `page` does, and also when a `QueryResult` has an empty `result_manifest` (a run a hook withheld) or its `result_id` is not in it: the chart is drawn only from results the caller was given. A `trace_id` alone is checked like `page`, by tenant and run id. |

The first three are described with `run_query` in [Query API](query.md).