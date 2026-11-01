# Dashboards and workspaces API

Source: `packages/api/src/nl2sql_api/routes/dashboards.py`, `routes/workspaces.py`, `models/dashboards.py` and the
generate job in `routes/jobs.py`.

These routes are thin over `engine.dashboards` and `engine.workspaces` (see [Dashboards](../../dashboards/index.md)):
each handler passes the request's caller and calls one named method, and none calls a model. A dashboard is built as a
draft in chat (`dashboard_edit`) and saved whole by `pin`; a workspace is a named group of the caller's own
dashboards. Both are private to their owner: another caller's dashboard or workspace is a `404`, the same answer as one
that does not exist. A request body is strict: an unknown field is a `422`. Responses are the engine's own models
(`Dashboard`, `DashboardView`, `DashboardRun`, `Workspace`), served as they are.

There is no route that edits what data a chart shows (`add_tile`, `replace_plan`) and none that shares a dashboard:
those exist only in chat, and dashboards stay private.

## Dashboards

| Route | Does |
|---|---|
| `GET /api/v1/dashboards` | The caller's dashboards, newest first. Query `workspace_id` and `status` (`saved`, the default, or `draft`). Returns `DashboardInfo[]`. |
| `GET /api/v1/dashboards/{id}` | Opens one: a `DashboardView` with the stored `TileRun`s, `refreshed_at` and `due`. It only reads. Nothing is refreshed and no query runs; call the refresh route for the charts in `due`. A chart the caller can no longer read is `denied`, one whose plan no longer validates is `needs_fixing`, both with no rows. |
| `POST /api/v1/dashboards/{id}/refresh` | Re-runs the stored plans as the caller. Body `{tile_id?, filters?, approval_ids?}`, all optional. `tile_id` limits it to one chart; `filters` maps a filter id to its values and the result is returned, never stored; `approval_ids` are request ids of approvals granted through `/approvals`. Returns a `DashboardRun`. Synchronous: it makes no model call. A dashboard someone else is refreshing is a `409`; an id in `approval_ids` that is unknown, waiting, denied or expired is a `404` and nothing runs. |
| `GET /api/v1/dashboards/{id}/filters/{filter_id}/values` | The distinct values of an `in` filter's column, or `[min, max]` of a `between` one, as the caller may read them. Query `limit` (1 to 1,000, default 100). Returns a list. |
| `PUT /api/v1/dashboards/{id}/title` | Body `{title}`. Returns the `Dashboard`. |
| `PUT /api/v1/dashboards/{id}/tiles/{tile_id}/title` | Body `{title}`. |
| `PUT /api/v1/dashboards/{id}/tiles/{tile_id}/chart` | Body `{chart}`: `auto`, `line`, `bar`, `big_number` or `table`. A kind the chart's last result cannot be drawn as is a `422` `CHART_NOT_SUPPORTED` naming the kinds it can. |
| `DELETE /api/v1/dashboards/{id}/tiles/{tile_id}` | Removes the chart and its stored result: how a `needs_fixing` chart is taken off. |
| `PUT /api/v1/dashboards/{id}/layout` | Body `{arrangements: [{tile_id, x, y, w, h}]}` on the 4-column grid (`x + w` at most 4). |
| `PUT /api/v1/dashboards/{id}/filters` | Body `{filters: [{id, label, column, kind, op}]}`, `op` being `in` or `between`. |
| `PUT /api/v1/dashboards/{id}/schedule` | Body `{every_minutes}`; `null` stops the schedule (the field is required). Below `dashboards.min_refresh_minutes` is a `422` `SCHEDULE_TOO_FREQUENT`. |
| `POST /api/v1/dashboards/{id}/pin` | Saves a draft whole. Optional body `{workspace_id?, title?}`; without a workspace, the one the draft is in, else "General". A saved dashboard or an empty draft is a `422` `NOT_PINNABLE`. |
| `POST /api/v1/dashboards/{id}/move` | Body `{workspace_id}`: one of the caller's workspaces. |
| `DELETE /api/v1/dashboards/{id}` | Deletes it, its stored results and its schedule. `204`. |

Every edit returns the updated `Dashboard`. An edit or refresh of a dashboard that is being refreshed is a `409`
`CONFLICT`.

## Generate a starter dashboard

`POST /api/v1/jobs/dashboard` is a [job](jobs.md): it answers `202` with the queued `Job`. Body:

```json
{"datasource_id": "chinook", "title": "Sales", "workspace_id": "w1", "max_tiles": 8}
```

Only `datasource_id` is required. The runner calls `engine.dashboards.generate` as the caller (no model is called), so
only the tables and columns the caller can read are charted. `progress` has one step per chart (`tile 3 of 8`), the job
can be cancelled between charts, and `result` is `{dashboard, run}`: the saved `Dashboard` and its first
`DashboardRun`. Any caller may start one, and the dashboard is theirs. No index is a `422` `NOT_INDEXED` and a
datasource with nothing to chart a `422` `NO_TILES`, both as the job's `error`.

## Starter questions

`GET /api/v1/datasources/{id}/starter-questions?n=5` returns the questions of the first `n` charts the starter
dashboard would build, as the caller may read the datasource, without running or saving anything. The list is empty
when the caller reaches no table; a datasource with no index (or one the caller cannot reach) is a `422`
`NOT_INDEXED`. It needs a caller: no identity is a `401`.

## Workspaces

| Route | Does |
|---|---|
| `GET /api/v1/workspaces` | The caller's workspaces, oldest first. |
| `POST /api/v1/workspaces` | Body `{name}`. `201` with the `Workspace`. Names are unique per owner, ignoring case. |
| `PUT /api/v1/workspaces/{id}/name` | Body `{name}`. |
| `DELETE /api/v1/workspaces/{id}` | An empty workspace only; one that still holds dashboards is a `422` `WORKSPACE_NOT_EMPTY`. `204`. |

## Errors

| Status | When |
|---|---|
| `404` `NOT_FOUND` | No such dashboard or workspace, or one that belongs to someone else; on refresh, also an unknown, waiting, denied or expired approval id. |
| `409` `CONFLICT` | The dashboard is being refreshed or edited. |
| `422` | `NOT_PINNABLE`, `CHART_NOT_SUPPORTED`, `UNKNOWN_TILE`, `UNKNOWN_FILTER`, `SCHEDULE_TOO_FREQUENT`, `WORKSPACE_NOT_EMPTY`, `NOT_INDEXED`, `NO_TILES`, `INVALID_ARGUMENT`, or `INVALID_REQUEST` for a body that does not validate. |

See [Errors](errors.md) for the envelope.
