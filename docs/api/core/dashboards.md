# Dashboards API

!!! note "Arrives with D1 to D3"
    A stub: every method has its final signature and raises `NotImplementedError` naming its row.

## Purpose
`engine.workspaces` and `engine.dashboards` ([Dashboards](../../dashboards/index.md)).

## Key Modules
- `packages/nl2sql/src/nl2sql/api/workspaces_api.py` (`WorkspacesAPI`, internal)
- `packages/nl2sql/src/nl2sql/api/dashboards_api.py` (`DashboardsAPI`, internal)
- `packages/nl2sql/src/nl2sql/dashboards/models.py` (`Workspace`, `Dashboard`, `Tile`, `DashboardEdit`)

## Public Surface

Every method takes `user_context` keyword-only and required, except `run_scheduled`, which runs each
dashboard as its stored owner. Another owner's workspace or dashboard is `WorkspaceNotFound` or
`DashboardNotFound`.

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