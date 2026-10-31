# Results API

## Purpose
`engine.results`: the results a run saved, read back by its `trace_id`, and the chart their data is drawn as.
Readable only by the caller who asked the run.

## Key Modules
- `packages/nl2sql/src/nl2sql/api/results_api.py` (`ResultsAPI`, internal)
- `packages/nl2sql/src/nl2sql/execution/artifacts/results.py` (`ResultManifest`, `ResultOwner`, `ResultPage`, `ResultReader`)
- `packages/nl2sql/src/nl2sql/charts/spec.py` (`ChartSpec`)

## Public Surface

| Method | What it does |
|---|---|
| `manifest(run_id, *, user_context) -> ResultManifest` | Which results the run saved: per result its node id, columns, row count and truncated flag, and who asked (`owner`). Never the rows. |
| `page(run_id, result_id, offset=0, limit=100, *, user_context) -> ResultPage` | Rows `offset` to `offset + limit` of one saved result, read lazily from its parquet file. |
| `csv(run_id, result_id, *, user_context) -> Iterator[bytes]` | The whole result as UTF-8 CSV, a slice at a time. |
| `chart(result, result_id=None, *, user_context, kind=None) -> ChartSpec` | The chart the result is drawn as ([Charts](../../charts/index.md)): a `ChartSpec` chosen by rules from the saved result's column kinds, with no model call. `result` is a `QueryResult` (a single sub-query's plan refines the kinds, and its intent titles the chart) or a `trace_id`. `result_id` may be left out when the run saved one result. `kind` forces `line`, `bar`, `big_number` or `table`; one the data cannot draw is a `ValueError` naming those it can. Raises `ResultNotFound` as `page` does, and also when a `QueryResult` has an empty `result_manifest` (a run a hook withheld) or its `result_id` is not in it: the chart is drawn only from results the caller was given. A `trace_id` alone is checked like `page`, by caller and run id; a run a hook withheld is `ResultNotFound` for every read, as `manifest`, `page` and `csv` also raise it. |

The first three are described with `run_query` in [Query API](query.md).

The run's trace is bound to the same asker: it records `owner` (the same `ResultOwner`), and
`engine.traces.get(trace_id, *, user_context=None)` answers only that asker, with `None` for any other
caller's run. No `user_context` is the trusted in-process operator (`nl2sql trace`), which reads any run.
`engine.feedback.record(trace_id, rating, *, user_context)` reads the run's trace as the caller and raises
`TraceNotFound` for another caller's run ([Feedback](../../observability/feedback.md)).

## Who can read a run

Every run writes `manifest.json` beside its results, and the manifest records who asked: a `ResultOwner`, the
caller's `tenant_id` (else the engine's `settings.tenant_id`) and `user_id`, and for an anonymous caller its
`roles`, sorted. Every read goes through the manifest, and every method takes the caller as a required keyword,
`user_context`:

- A named user who asked reads the run: the same `user_id` and the same tenant, whatever their roles are now.
- An anonymous caller (no `user_id`) reads a run asked anonymously in the same tenant with the same set of roles.
- Any other caller is `ResultNotFound`, exactly as for a run that does not exist: another user of the same tenant,
  the same user id in another tenant, an anonymous caller reading a named user's run, or an anonymous caller with
  other roles. A trace id appears in events, traces and the audit log, so it is never the access check.
- No caller is a `TypeError` (`user_context` missing, `None`, or not a `UserContext`). There is no default caller,
  as for conversations and dashboards: failing open would let anyone read a run whose trace id they saw.
- A manifest that records no asker (one written before 0.9) is `ResultNotFound` for every caller. There is no
  legacy path; run the question again.

**Anonymous callers.** A run asked with no `user_id` (a `UserContext` with only roles, or `run_query` with no
`user_context`) is owned by its tenant and its sorted roles. It is readable by any caller of that tenant who also
has no `user_id` and has the same roles, and by no named user. So a `viewer` holding an `admin` run's trace id
reads nothing, while anonymous callers with the same roles share each other's results, because nothing else tells
them apart.

The REST API has no per-user identity yet: its static or header role (and the playground's role picker) build a
`UserContext` with roles only. So every REST run is anonymous, and readable by any REST caller of the same tenant
with the same roles. API keys carry a `user_id`, but only to the MCP server; REST does not take them. To keep
callers' results apart, call the engine in process with a `user_id` from your own auth.

**Operator tools.** `nl2sql run`, `nl2sql trace`, `nl2sql eval`, the replay provider and the feedback tools read
the result they were just given, traces and recordings, never `engine.results`, so they are unchanged. In-process
code is trusted with the `UserContext` it passes (Wave A's D24): an operator who needs a past run's rows reads them
as its asker, a `UserContext` with that caller's `user_id` and `tenant_id`.

```python
from nl2sql import UserContext

ann = UserContext(user_id="ann", roles=["analyst"])
result = engine.run_query("Revenue by month", user_context=ann)
page = engine.results.page(result.trace_id, result.result_manifest[0].node_id, user_context=ann)
engine.results.page(result.trace_id, "combine_g1", user_context=UserContext(user_id="bob"))  # ResultNotFound
```
