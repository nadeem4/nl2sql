# Dashboards

!!! note "Partly built (Wave C, 0.9)"
    Workspaces, the dashboard store, `pin`, `move` and the manual edits work (D1). Refresh, filters and
    schedules (D2), the starter dashboard (D3) and building in chat (D4) have their signatures and raise
    `NotImplementedError` naming the pull request that fills them. The design is
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

## Workspaces

A workspace is a named folder of your dashboards ("Sales", "Ops"). A dashboard lives in exactly one, and
both are private to you: someone else's is "not found". `engine.workspaces.create`, `rename`, `list` and
`delete` manage them; `delete` refuses a workspace that still holds a dashboard, draft or saved. The first
time you pin with no workspace in mind, a workspace named "General" is made for you.

## Drafts and pinning

A dashboard built in chat is a **draft** tied to its conversation. It is listed only with
`list(status="draft")` and is deleted by `engine.storage.purge()` once it has not changed for
`conversations.max_age_days` (30). `pin` saves the **whole** dashboard (status `saved`), in the workspace you
name, else the one it is already in, else "General". A chart is never pinned alone. A saved dashboard is
never purged.

```python
ws = engine.workspaces.create("Sales", user_context=me)
board = engine.dashboards.pin(draft.id, user_context=me, workspace_id=ws.id, title="Revenue")
engine.dashboards.move(board.id, other.id, user_context=me)
```

## Editing by hand

Every change a person can make is a typed edit, and no edit calls a model or takes a question or a plan:

| Edit | Method | What it does |
|---|---|---|
| `rename` | `rename(id, title)` | Renames the dashboard |
| `rename_tile` | `rename_tile(id, tile_id, title)` | Renames one chart |
| `set_tile_chart` | `set_tile_chart(id, tile_id, chart)` | `auto`, `line`, `bar`, `big_number` or `table`; a kind the last result cannot be drawn as is a `ValueError` naming the ones it can |
| `remove_tile` | `remove_tile(id, tile_id)` | Removes a chart and its stored result |
| `arrange` | `arrange(id, layouts)` | Moves or resizes charts on the 4-column grid; the others stay put |
| `set_filters` | `set_filters(id, filters)` | Stores the filter definitions (their values are applied by refresh, D2) |
| `set_schedule` | `set_schedule(id, every_minutes)` | Stores a schedule and a snapshot of your user, tenant and roles; `None` removes it. Below `dashboards.min_refresh_minutes` (15) is `ScheduleTooFrequent` |
| `move` | `move(id, workspace_id)` | Moves it to another of your workspaces |

`edit(id, [RenameDashboard(...), RemoveTile(...)], user_context=me)` applies a list in order and **all or
none**: if one edit is refused, nothing is saved. A draft and a saved dashboard take the same edits. Stored
chart results stay valid across a rename, a redraw, an arrange, a move and a schedule; removing a chart or
changing the filters discards the results they invalidate. `add_tile` and `replace_plan`, which change what
data a chart shows, are only for chat (D4).

Each change publishes `dashboard.changed` (the kinds, never the values), `set_schedule` also
`dashboard.schedule_set` (it saves a role snapshot, so it is audited) and the workspace calls publish
`workspace.changed`.

## Configuration

`dashboards:` in `nl2sql.yaml` (`max_tiles`, `min_refresh_minutes`, `schedule_identity_max_age_days`) is
accepted; `min_refresh_minutes` is read by `set_schedule`.