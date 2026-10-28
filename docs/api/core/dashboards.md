# Dashboards API

!!! note "Partly built"
    The workspaces, the dashboard store, `pin`, `move` and the manual edits work (D1), and so do `open`,
    `refresh` and `run_scheduled` (D2), and `generate` and `suggest_questions` (D3). `filter_values` and
    `refresh(filters=...)` (D2b) have their final signatures and raise `NotImplementedError` naming their row.

## Purpose
`engine.workspaces` and `engine.dashboards` ([Dashboards](../../dashboards/index.md)).

## Key Modules
- `packages/nl2sql/src/nl2sql/api/workspaces_api.py` (`WorkspacesAPI`, internal)
- `packages/nl2sql/src/nl2sql/api/dashboards_api.py` (`DashboardsAPI`, internal)
- `packages/nl2sql/src/nl2sql/dashboards/models.py` (`Workspace`, `Dashboard`, `Tile`, `DashboardEdit`)
- `packages/nl2sql/src/nl2sql/dashboards/` `workspaces.py`, `api.py`, `edits.py`, `store.py`, `run.py` (running the
  charts), `schedule.py`, and for the starter dashboard `roles.py`, `templates.py` and `layout.py` (the logic
  behind the two faces)

## Public Surface

Every method takes `user_context` keyword-only and required, except `run_scheduled`, which runs each
dashboard as its stored owner. Another owner's workspace or dashboard is `WorkspaceNotFound` or
`DashboardNotFound`. A missing caller (`user_context=None`) is a `TypeError`, not an anonymous owner.

## Behaviour

- **Private.** A workspace and its dashboards belong to the `(tenant_id, user_id)` that made them; anyone
  else's is `WorkspaceNotFound` or `DashboardNotFound`, never "forbidden".
- **Workspaces.** `create` and `rename` refuse an empty name and a name the owner already uses (ignoring case).
  `delete` raises `WorkspaceNotEmpty` while a dashboard, draft or saved, is in it. They are listed oldest first.
- **`list`** returns one `status` (`saved` by default, or `draft`) and, with `workspace_id`, one workspace,
  newest first. `refreshed_at` is the oldest stored result of its charts (None until a refresh stores some).
- **`pin`** saves a draft whole, in `workspace_id`, else the workspace the draft is in, else "General" (made on
  first use). There is no way to pin one chart. An empty draft, a dashboard already saved, or a blank `title`
  is a `ValueError`.
- **`edit`** applies a list of edits in order, each seeing the ones before, all or none. The named methods
  (`rename`, `rename_tile`, `set_tile_chart`, `remove_tile`, `arrange`, `set_filters`, `set_schedule`, `move`)
  each build one edit. None calls a model, and none takes a question or a plan: what data a chart shows changes
  only in chat (`add_tile`, `replace_plan`, D4). An unknown chart id is a `ValueError`.
- **`set_tile_chart`** with a kind the chart's last result cannot be drawn as raises a `ValueError` naming the
  kinds it can be, judged from the column kinds stored with that result (so a result last drawn as a table can
  still be drawn as a bar). With no stored result any kind is accepted.
- **`set_schedule(every_minutes)`** below `dashboards.min_refresh_minutes` is `ScheduleTooFrequent`; otherwise it
  stores the schedule with the caller's `user_id`, `tenant_id` and roles (D134), and `None` removes it. It runs
  only while a long-running process calls `run_scheduled` (`nl2sql-api` does, every minute).
- **Stored results.** `remove_tile` discards that chart's stored result and `set_filters` (when the filters
  change) every chart's; renaming, redrawing, arranging, moving and scheduling keep them.
- **`open(dashboard_id, refresh_due=True)`** checks access first: each stored plan is checked under the caller's
  current roles, running no query (a generated plan is validated and generated; a verified query is checked by
  the curated matcher). It returns `DashboardView(dashboard, runs, refreshed_at, due)`. `runs` holds the stored
  result of each chart this `open` checked: as stored when the check ended `ok`, `empty`, or `needs_approval` with
  no other error; otherwise the check's result (`denied`, `needs_fixing`, `error`, ...) without rows. A denial
  wins: a chart with one query refused, or one a hook dropped, is `denied`. A result another process stored after
  the check is not served; the chart stays in `due`. With
  `refresh_due`, the charts with no result or one older than the schedule are then refreshed (`trigger="open"`,
  as the caller, never approving); `due` lists the ids still due (an app that passed `False`, a chart waiting for
  approval, or a dashboard another process is refreshing).
- **`refresh(dashboard_id, tile_id=None, approvals=())`** runs the stored plans of one chart or all, in layout
  order, as the caller, and stores each result (`TileRun`: `at`, `status`, the chart with its rows, the
  `column_kinds`, the plain-English `explanation`, the `error`). Statuses: `ok`, `empty`, `needs_fixing` (a plan
  that no longer validates or parses; the refiner is never asked), `denied`, `needs_approval` (its requests in
  `approvals_needed`; the stored result is kept), `stopped` (a cap; the remaining charts are `stopped` and keep
  theirs) and `error` (returned, not stored: the chart keeps its last good result). It raises `DashboardBusy` (a `ValueError`) while the dashboard is being refreshed or
  edited, and `ValueError` for a `tile_id` the dashboard does not have. `filters` is D2b's.
- **`run_scheduled(now=None)`** has no caller: it refreshes each dashboard whose schedule is due, as its saved
  identity, and returns the `DashboardRun`s (`trigger="schedule"`). A schedule that fails a check stops, with
  `state="stopped"` and `error={"code", "message"}` on `Dashboard.schedule`, and `dashboard.schedule_stopped`.
  The codes are `SCHEDULE_IDENTITY_EXPIRED`, `SCHEDULE_ROLE_REMOVED`, `SCHEDULE_ACCESS_DENIED` (a chart the owner
  can no longer read) and `SCHEDULE_HOOK_DENIED` (a hook denied a chart; the message names the chart and the
  hook's reason). A `now` without a timezone is a `ValueError`.
- **`generate(datasource_id, title=None, workspace_id=None, max_tiles=None)`** builds the no-model starter
  dashboard and returns `(Dashboard, DashboardRun)` ([how it chooses](../../dashboards/index.md#the-starter-dashboard)).
  The roles come from the indexed snapshot and the glossary, only for tables the caller can read; each template chart
  runs as the caller (`trigger="generate"`); the charts whose first run is `ok` are kept and the others are in
  `DashboardRun.skipped` (`title`, `status`, `reason`). The result is saved, owned by the caller, in `workspace_id`
  or "General". Raises `NotIndexed` (names `nl2sql index --datasource <id>`), `NoTiles` (no table the caller
  reaches, or no chart answered), `WorkspaceNotFound`, and `ValueError` for `max_tiles` below 1.
  **`suggest_questions(datasource_id, n=5)`** returns the questions of the first `n` charts `generate` would
  choose and runs and saves nothing (`[]` when the caller reaches no table; `NotIndexed` as above).
- **A verified chart** keeps its datasource in `ChartPlan.verified_datasource_id`, required with `verified`.
- **Events.** `dashboard.changed` (the edit kinds, `pinned` or `deleted`, and `by`), `dashboard.schedule_set`,
  `dashboard.generated`, `dashboard.refreshed` (the counts per status), `dashboard.schedule_stopped` and `workspace.changed`. They
  carry no titles, names or values.
- **Retention.** `engine.storage.purge()` deletes drafts last edited more than `conversations.max_age_days` ago;
  saved dashboards are never purged.

## Methods

| Method | Row |
|---|---|
| `workspaces.create(name, *, user_context)`, `rename(workspace_id, name, *, user_context)`, `list(*, user_context)`, `delete(workspace_id, *, user_context)` | D1 |
| `dashboards.get(dashboard_id, *, user_context)`, `list(*, user_context, workspace_id=None, status="saved")`, `delete(...)` | D1 |
| `dashboards.pin(dashboard_id, *, user_context, workspace_id=None, title=None)`, `move(dashboard_id, workspace_id, *, user_context)` | D1 |
| `dashboards.edit(dashboard_id, edits, *, user_context)` and the named edits `rename`, `rename_tile`, `set_tile_chart`, `remove_tile`, `arrange`, `set_filters`, `set_schedule` | D1 |
| `dashboards.open(dashboard_id, *, user_context, refresh_due=True) -> DashboardView` | D2 |
| `dashboards.refresh(dashboard_id, *, user_context, tile_id=None, filters=None, approvals=()) -> DashboardRun` | D2 |
| `dashboards.filter_values(dashboard_id, filter_id, *, user_context, limit=100)` | D2b |
| `dashboards.run_scheduled(*, now=None) -> list[DashboardRun]` | D2 |
| `dashboards.generate(datasource_id, *, user_context, title=None, workspace_id=None, max_tiles=None)` | D3 |
| `dashboards.suggest_questions(datasource_id, *, user_context, n=5) -> list[str]` | D3 |