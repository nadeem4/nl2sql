# Dashboards

!!! note "Arrives with D1 to D4 (Wave C, 0.9)"
    This page is a stub. The models (`Workspace`, `Dashboard`, `Tile`, `DashboardEdit` and the rest) and
    the `engine.workspaces` and `engine.dashboards` signatures exist; each method raises
    `NotImplementedError` naming the pull request that fills it. The design is
    [Wave C, section 6](../architecture/wave_c.md#6-dashboards).

A dashboard is a set of charts, each holding the typed plan that produced it, saved whole into a
private workspace. Opening one serves stored results after the access check; refreshing re-runs the
stored plans as the caller with no model call. The methods are in
[Dashboards API](../api/core/dashboards.md).

| Row | What it adds |
|---|---|
| D1 | Workspaces, the dashboard store, `pin`, `move` and the manual edits |
| D2 | `open`, `refresh`, stored results, filters, schedules and `nl2sql dashboards refresh` |
| D3 | The starter dashboard: `generate` and `suggest_questions`, with no model |
| D4 | Building and editing a dashboard in a conversation (`dashboard_edit`, `dashboard_pin`) |

`dashboards:` in `nl2sql.yaml` (`max_tiles`, `min_refresh_minutes`, `schedule_identity_max_age_days`) is
already accepted.