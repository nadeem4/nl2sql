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
| `chart(result, result_id=None, *, kind=None, tenant_id=None) -> ChartSpec` | The chart the result is drawn as ([Charts](../../charts/index.md)). Arrives with K1; raises `NotImplementedError` until then. |

The first three are described with `run_query` in [Query API](query.md).