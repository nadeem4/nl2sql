# Dashboards API

!!! note "Partly built"
    The workspaces, the dashboard store, `pin`, `move` and the manual edits work, and so do `open`,
    `refresh` and `run_scheduled`, `generate` and `suggest_questions`, and `filter_values` and
    `refresh(filters=...)`. A conversation builds and edits a draft with the `dashboard_edit` and
    `dashboard_pin` chat tools.

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
- **`create_draft(title=None, workspace_id=None, conversation_id=None)`** makes an empty draft owned by the caller
  (title "New dashboard"; in `workspace_id`, else "General", made on first use) and publishes `dashboard.changed`
  (`created`). The `dashboard_edit` chat tool calls it on first use, with the conversation's id, which is stored as
  `Dashboard.conversation_id`; a `conversation_id` that is not the caller's is `ConversationNotFound`. The tool also
  makes a new draft when the conversation's dashboard has been deleted.
- **`edit(dashboard_id, edits, conversation=None)`** applies a list of edits in order, each seeing the ones before,
  all or none. The named methods (`rename`, `rename_tile`, `set_tile_chart`, `remove_tile`, `arrange`,
  `set_filters`, `set_schedule`, `move`) each build one edit. None calls a model, and none takes a question or a
  plan: what data a chart shows changes only through a conversation step (`add_tile`, `replace_plan`) or by naming
  a reviewed query or the caller's own saved answer by id (`add_verified_tile`, `add_saved_tile`; a verified id
  that `curated.list` does not show the caller is "no verified query"). `add_tile` and `replace_plan` read an `ask` step of
  the `conversation` passed (it must be the caller's, else `ValueError`; the chat tool passes the one it runs in)
  and are a `ValueError` without one. The step's whole decomposition and each sub-query's plan, or the verified
  query that answered, become the chart's plan; it is first checked by a run that executes nothing, as the caller
  and under the dashboard's lock, and a plan the validator rejects or the caller may not read raises a
  `ValueError` that ends with "Nothing was saved." An unknown chart id is a `ValueError`. `from_step.turn` defaults
  to the conversation's latest turn. **`conversation` is trusted:** its steps are not re-read from the store, only
  its owner is checked against the caller, so it is for SDK code holding the caller's own `Conversation` and is
  not exposed over REST or MCP; the plan's access check still runs as the caller. The lock lasts as long as the
  edit's checks (one time limit per `add_tile` or `replace_plan`).
  Two more edits add a chart without a conversation step and call no model: `add_verified_tile`
  (`datasource_id`, `verified_id`, `title`, `params`, `question`) from a verified query of the curated section (an
  unknown id or parameter, or a missing parameter without a default, is `InvalidArgument`), and `add_saved_tile`
  (`saved_id`, `title`) from the caller's own saved answer (another user's is `AnswerNotFound`); the saved plan is
  copied, so later changes to the answer do not touch the chart. Both get the same access check as `add_tile`,
  and the chat tool `dashboard_edit` accepts them.
- **`set_tile_chart`** with a kind the chart's last result cannot be drawn as raises a `ValueError` naming the
  kinds it can be, judged from the column kinds stored with that result (so a result last drawn as a table can
  still be drawn as a bar). With no stored result any kind is accepted.
- **`set_schedule(every_minutes)`** below `dashboards.min_refresh_minutes` is `ScheduleTooFrequent`; otherwise it
  stores the schedule with the caller's `user_id`, `tenant_id` and roles, and `None` removes it. It runs
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
  edited, `ApprovalNotFound` for a request id in `approvals` that is unknown, waiting, denied or expired (nothing runs), and `ValueError` for a `tile_id` the dashboard does not have.
- **`filters`** maps filter ids to values: `refresh(dashboard_id, filters={"country": ["Brazil"]})`. Each
  value is checked against the filter's kind first (`number`, `temporal` as `YYYY-MM-DD` or
  `YYYY-MM-DDTHH:MM[:SS[.ffffff]]` with an optional `Z` or `+HH:MM`, `boolean`, `text`; `in` takes 1 to 100 values,
  `between` exactly two), and a wrong one, or an id the dashboard does not have, is a `ValueError` naming the
  filter, raised before any chart runs. The predicate (`between` as `>=` and `<=`, with a date-only upper bound as
  `<` the next day; `in` as `OR`s; the values as plan literals) is added to each stored query that reads the
  filter's table (schema and name; a plan table with no schema never matches) before the plan goes to
  `run_query`; other queries run unfiltered. `TileRun.filtered` is true when every query of the chart got a filter
  and every active filter reached the chart, `DashboardRun.filters` holds the values and `dashboard.refreshed`
  the ids. A filtered refresh is returned, not stored, and takes no refresh lock (an unfiltered one does).
- **`filter_values(dashboard_id, filter_id, *, user_context, limit=100)`** returns the distinct values of an `in`
  filter's column (at most `limit`, 1 to 1,000, in order) or `[min, max]` of a `between` one (`[]` when there are
  none), by a plan built in code and run as the caller through `run_query`, so the caller's scope and policies
  apply and no model is called. A column the caller cannot read, or an unknown filter, is a `ValueError`.
- **`run_scheduled(now=None)`** has no caller: it refreshes each dashboard whose schedule is due, as its saved
  identity, and returns the `DashboardRun`s (`trigger="schedule"`). A schedule that fails a check stops, with
  `state="stopped"` and `error={"code", "message"}` on `Dashboard.schedule`, and `dashboard.schedule_stopped`.
  The codes are `SCHEDULE_IDENTITY_EXPIRED`, `SCHEDULE_ROLE_REMOVED`, `SCHEDULE_ACCESS_DENIED` (a chart the owner
  can no longer read) and `SCHEDULE_HOOK_DENIED` (a hook denied a chart; the message names the chart and the
  hook's reason). A `now` without a timezone is a `ValueError`.
- **`generate(datasource_id, title=None, workspace_id=None, max_tiles=None, on_tile=None, cancellation_token=None)`** builds the no-model starter
  dashboard and returns `(Dashboard, DashboardRun)` ([how it chooses](../../dashboards/index.md#the-starter-dashboard)).
  The roles come from the indexed snapshot and the glossary, only for tables the caller can read; each template chart
  runs as the caller (`trigger="generate"`); the charts whose first run is `ok` are kept and the others are in
  `DashboardRun.skipped` (`title`, `status`, `reason`). The result is saved, owned by the caller, in `workspace_id`
  or "General". Raises `NotIndexed` (names `nl2sql index --datasource <id>`), `NoTiles` (no table the caller
  reaches, or no chart answered), `WorkspaceNotFound`, and `ValueError` for `max_tiles` below 1.
  `on_tile` is called with a `TileProgress` (`title`, `status`, `done`, `total`) after each chart ran. Cancelling
  `cancellation_token` stops before the next chart and raises `nl2sql.RunCancelled`; the dashboard is not saved, no
  tile result is stored, and no draft or workspace this call made is left (a chart that already ran keeps its query
  trace, as any question does).
  **`suggest_questions(datasource_id, n=5)`** returns the questions of the first `n` charts `generate` would
  choose and runs and saves nothing (`[]` when the caller reaches no table; `NotIndexed` as above).
- **A verified chart** keeps its datasource in `ChartPlan.verified_datasource_id`, required with `verified`.
- **Events.** `dashboard.changed` (the edit kinds, `created`, `pinned` or `deleted`, and `by`), `dashboard.schedule_set`,
  `dashboard.generated`, `dashboard.refreshed` (the counts per status and the filter ids applied),
  `dashboard.schedule_stopped` and `workspace.changed`. They
  carry no titles, names or values.
- **Retention.** `engine.storage.purge()` deletes drafts last edited more than `conversations.max_age_days` ago;
  saved dashboards are never purged.

## Guided builder

`engine.dashboards.builder_options`, `propose_tiles` and `build` build a dashboard from choices, not from a
description. All take `user_context`, and the ids
they offer are limited to what the caller's scope reaches.

- **`builder_options(datasource_id, *, user_context) -> BuilderOptions`** returns `curated` (the datasource's
  verified queries with no required parameter, `VerifiedQueryInfo`), `saved` (the caller's own saved answers),
  `metrics` (glossary terms whose columns the caller may read; a term with any hidden column is left out whole),
  `dimensions` and `time_columns` (from the column roles) and `ranges` (`last_7_days`, `last_30_days`,
  `last_90_days`, `last_12_months`, `this_year`). It calls no model.
- **`propose_tiles(datasource_id, *, user_context, sources, curated_ids=(), saved_ids=(), metrics=(),
  dimensions=(), time_range=None, text=None, llm_override=None) -> list[TileProposal]`** reads and writes
  nothing. `sources` is any of `curated`, `saved`, `asked`. Curated and saved proposals use no model. `asked` makes
  one model call for at most 3 new questions (from the chosen metrics and dimensions, the time range and the
  free `text`). The list is capped at `dashboards.max_tiles`. Without a key, `asked` raises `MissingProviderKey`;
  curated and saved still work. `llm_override` is the caller's own key, as for `ask`.
- **`build(datasource_id, tiles, *, user_context, title=None, workspace_id=None, time_column=None, on_tile=None,
  cancellation_token=None, llm_override=None) -> BuildReport`** takes the ticked `TileProposal`s (more than
  `max_tiles` is `InvalidArgument`) and makes a draft: curated and saved tiles through the two edits above, asked
  tiles as `ask` steps of one builder conversation joined through `add_tile` (that conversation is deleted
  afterwards). A question that cannot be answered is reported in `BuildReport.skipped` (`question`, `code`,
  `message`) and the rest are kept; nothing built raises `NoTiles`; a cancel or failure leaves nothing behind.
  With `time_column` a `between` filter on it is added. It then pins the draft, publishes `dashboard.generated`
  and returns `BuildReport(dashboard, built, skipped)`. It does not run the tiles; opening refreshes them. `on_tile`
  receives a `TileProgress` per tile.
- **`engine.curated.list(*, user_context, datasource_id=None) -> list[VerifiedQueryInfo]`** lists the verified
  queries the caller's scope reaches, with their parameters.

## Methods

| Method |
|---|
| `workspaces.create(name, *, user_context)`, `rename(workspace_id, name, *, user_context)`, `list(*, user_context)`, `delete(workspace_id, *, user_context)` |
| `dashboards.get(dashboard_id, *, user_context)`, `list(*, user_context, workspace_id=None, status="saved")`, `delete(...)` |
| `dashboards.pin(dashboard_id, *, user_context, workspace_id=None, title=None)`, `move(dashboard_id, workspace_id, *, user_context)` |
| `dashboards.edit(dashboard_id, edits, *, user_context)` and the named edits `rename`, `rename_tile`, `set_tile_chart`, `remove_tile`, `arrange`, `set_filters`, `set_schedule` |
| `dashboards.open(dashboard_id, *, user_context, refresh_due=True) -> DashboardView` |
| `dashboards.refresh(dashboard_id, *, user_context, tile_id=None, filters=None, approvals=()) -> DashboardRun` (`approvals` are `Approval` objects or granted request ids) |
| `dashboards.filter_values(dashboard_id, filter_id, *, user_context, limit=100) -> list` |
| `dashboards.run_scheduled(*, now=None) -> list[DashboardRun]` |
| `dashboards.generate(datasource_id, *, user_context, title=None, workspace_id=None, max_tiles=None, on_tile=None, cancellation_token=None)` |
| `dashboards.suggest_questions(datasource_id, *, user_context, n=5) -> list[str]` |
| `dashboards.builder_options(...)`, `propose_tiles(...)`, `build(...)`; edits `add_verified_tile`, `add_saved_tile`; `curated.list(...)` |