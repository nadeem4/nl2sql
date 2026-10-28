# Dashboards API

!!! note "Partly built"
    The workspaces, the dashboard store, `pin`, `move` and the manual edits work (D1). `open`, `refresh`,
    `filter_values` and `run_scheduled` (D2) and `generate` and `suggest_questions` (D3) have their final
    signatures and raise `NotImplementedError` naming their row.

## Purpose
`engine.workspaces` and `engine.dashboards` ([Dashboards](../../dashboards/index.md)).

## Key Modules
- `packages/nl2sql/src/nl2sql/api/workspaces_api.py` (`WorkspacesAPI`, internal)
- `packages/nl2sql/src/nl2sql/api/dashboards_api.py` (`DashboardsAPI`, internal)
- `packages/nl2sql/src/nl2sql/dashboards/models.py` (`Workspace`, `Dashboard`, `Tile`, `DashboardEdit`)
- `packages/nl2sql/src/nl2sql/dashboards/` `workspaces.py`, `api.py`, `edits.py`, `store.py` (the logic behind the two faces)

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
  newest first. `refreshed_at` is the oldest stored result of its charts (None until D2 stores some).
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
  stores the schedule with the caller's `user_id`, `tenant_id` and roles (D134), and `None` removes it. Nothing
  runs yet: D2's scheduler does.
- **Stored results.** `remove_tile` discards that chart's stored result and `set_filters` (when the filters
  change) every chart's; renaming, redrawing, arranging, moving and scheduling keep them.
- **Events.** `dashboard.changed` (the edit kinds, `pinned` or `deleted`, and `by`), `dashboard.schedule_set`
  and `workspace.changed`. They carry no titles, names or values.
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
| `dashboards.filter_values(dashboard_id, filter_id, *, user_context, limit=100)` | D2 |
| `dashboards.run_scheduled(*, now=None) -> list[DashboardRun]` | D2 |
| `dashboards.generate(datasource_id, *, user_context, title=None, workspace_id=None, max_tiles=None)` | D3 |
| `dashboards.suggest_questions(datasource_id, *, user_context, n=5) -> list[str]` | D3 |