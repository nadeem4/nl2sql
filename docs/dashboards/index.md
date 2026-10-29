# Dashboards

!!! note "Partly built (Wave C, 0.9)"
    Workspaces, the dashboard store, `pin`, `move` and the manual edits work (D1), and so do `open`, `refresh`,
    stored results and schedules (D2), and so do `generate` and `suggest_questions`, the starter dashboard (D3),
    and building in chat (D4). Filter values and `filter_values` (D2b) have their signatures and raise
    `NotImplementedError` naming the pull request that fills them. The design is
    [Wave C, section 6](../architecture/wave_c.md#6-dashboards).

A dashboard is a set of charts, each holding the typed plan that produced it, saved whole into a
private workspace. Opening one serves stored results after the access check; refreshing re-runs the
stored plans as the caller with no model call. The methods are in
[Dashboards API](../api/core/dashboards.md).

| Row | What it adds |
|---|---|
| D1 | Workspaces, the dashboard store, `pin`, `move` and the manual edits |
| D2 | `open`, `refresh`, stored results, schedules and `nl2sql dashboards refresh` (filter values follow in D2b) |
| D3 | The starter dashboard: `generate` and `suggest_questions`, with no model |
| D4 | Building and editing a dashboard in a conversation (`dashboard_edit`, `dashboard_pin`) and `create_draft` |

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
| `set_filters` | `set_filters(id, filters)` | Stores the filter definitions (their values are applied by refresh, D2b) |
| `set_schedule` | `set_schedule(id, every_minutes)` | Stores a schedule and a snapshot of your user, tenant and roles; `None` removes it. Below `dashboards.min_refresh_minutes` (15) is `ScheduleTooFrequent` |
| `move` | `move(id, workspace_id)` | Moves it to another of your workspaces |

`edit(id, [RenameDashboard(...), RemoveTile(...)], user_context=me)` applies a list in order and **all or
none**: if one edit is refused, nothing is saved. A draft and a saved dashboard take the same edits. Stored
chart results stay valid across a rename, a redraw, an arrange, a move and a schedule; removing a chart or
changing the filters discards the results they invalidate. `add_tile` and `replace_plan`, which change what
data a chart shows, are only for a conversation (see [Building in chat](#building-in-chat)).

Each change publishes `dashboard.changed` (the kinds, never the values), `set_schedule` also
`dashboard.schedule_set` (it saves a role snapshot, so it is audited) and the workspace calls publish
`workspace.changed`.

An edit, `pin` and `delete` take the dashboard's refresh lock (see [Open, refresh and stored results](#open-refresh-and-stored-results)),
so a refresh that is running cannot store results after an edit has made them wrong. If the lock is held you
get a `ValueError` (`DashboardBusy`) that says the dashboard is being refreshed; try again when it is done.

## Building in chat

In a conversation the model builds a dashboard the way a person would: one question per chart, then the
`dashboard_edit` [chat tool](../conversations/tools.md#the-dashboard-tools) with an `add_tile` edit for each
answer it wants, and further edits to the same draft.

```python
chat = engine.conversations.start(user_context=me)
engine.conversations.ask(chat.id, "Build me a dashboard of orders by city and customers by country", user_context=me)
board = engine.dashboards.get(engine.conversations.get(chat.id, user_context=me).dashboard_id, user_context=me)
engine.conversations.tool(chat.id, "dashboard_pin", {"title": "Sales"}, user_context=me)   # only when asked
```

- **A draft on first use.** The first `dashboard_edit` makes an empty draft (`create_draft`) in "General" or the
  workspace named, tied to the conversation. A conversation started with `start(dashboard_id=...)` edits that
  dashboard instead. It stays a draft until `dashboard_pin` (the model calls it only when the user asks) saves it
  whole.
- **`add_tile`** takes a `title` and `from_step` (`turn`, which defaults to the turn being answered, and which of that turn's `ask` calls). The chart keeps
  the **whole** plan of that answer: its decomposition, the plan of every sub-query and the combine step, or the
  verified query that answered. The model never writes or sees a plan or SQL, and the engine reads the plan from
  its own stored step.
- **A chart is checked before it is saved.** The plan goes through a run that executes nothing (the check `open`
  uses), as the caller. If the validator rejects it, or the caller may not read it, nothing in that
  `dashboard_edit` call is saved and the model is told why.
- **Fixing a chart.** A chart that is `needs_fixing` is shown to the model in the open dashboard with the
  validator's reason. It asks the question again and calls `replace_plan` with the new answer: the chart keeps its
  id, title, chart type and place, takes the new question and plan, and its stored result is discarded. This is
  the only part of the dashboard that spends tokens.
- **The same edits as by hand.** `dashboard_edit` takes every manual edit in the table above, so the model and the
  person change a dashboard in the same ways and with the same checks. The model sees the open dashboard (charts
  with their ids, questions and last status) at every step, never a row.
- **The facade.** `engine.dashboards.edit(id, edits, user_context=me, conversation=chat)` takes the two chat-only
  edits when it is given a conversation of the caller's; without one they are a `ValueError`. The named methods
  take no question and no plan.

## Open, refresh and stored results

A chart is re-run from the plan it keeps, never by asking the question again, so **refreshing costs no
tokens**. Each chart is one `run_query(question, plan=SuppliedPlan(...), answer=False, user_context=...)`:
the plan goes through the `plan.after` hooks, the validator and the generator under the caller's scope,
is never cached and is never sent to the refiner, and no answer is written. A multi-query chart re-runs every
stored query and its combine step; a verified chart re-runs its verified query with its stored values.

```python
view = engine.dashboards.open(board.id, user_context=me)        # the stored results, after the access check
run = engine.dashboards.refresh(board.id, user_context=me)      # every chart, in layout order
run = engine.dashboards.refresh(board.id, tile_id="t3", user_context=me)   # one chart
```

| Rule | What it means |
|---|---|
| **Stored with a time** | The last result of each chart (its rows, at most `charts.max_points`, and the columns' kinds) is saved with an `at` time, replaced by each run and deleted with the dashboard. `open` serves it at once and `refreshed_at` is the oldest `at`. A run that fails (`error`, such as a database outage) does not replace it: the chart keeps its last good result and the refresh returns the error. |
| **Open checks access first** | Before any stored row is served, each stored plan is checked under the caller's current roles, and the check runs no query: a generated plan is validated and generated, a verified query is checked by the curated matcher (the caller's scope, its values, every table it reads) and by the `execute.before` hooks, its only hook point, leaving out the cost hook: a hook's `Deny` makes the chart `denied`, and a request for approval does not block the check, which runs nothing. Rows are served only when the check ends `ok` or `empty`, or `needs_approval` with nothing else wrong. A chart the caller can no longer read comes back `denied` (also when only one of its queries is refused, or a hook dropped one of them), one whose plan no longer validates `needs_fixing`, and a check that fails otherwise `error`, all without rows. Only charts this `open` checked, or refreshed itself, are served: a result another process stored meanwhile waits in `due`. |
| **Only what is old** | With `refresh_due=True` (the default) `open` then refreshes the charts older than the schedule's `every_minutes` (or never run), as the caller. A dashboard with no schedule has nothing due. An app that wants an instant open passes `False` and refreshes the ids in `view.due`. |
| **`needs_fixing`** | A stored plan that no longer validates (a column was dropped), or no longer parses, is not repaired by a model. The chart is `needs_fixing` with the validator's message and no rows, and the other charts keep working. Fixing it is a conversation turn: [`replace_plan`](#building-in-chat). |
| **Cost guard** | A hook that wants a person's yes makes the chart `needs_approval`: it is not run, keeps its last result, and the request is in the run's `approvals_needed`. Pass an `Approval` for it to a **manual** `refresh(approvals=[...])`. `open` and a schedule never approve. |
| **Caps** | A chart stopped at a Wave A cap (`stopped`) ends the refresh; the rest are `stopped` too and keep their last result. |
| **No double runs** | A refresh holds the state store's lock `dashboard-refresh:<id>`. A second `refresh` on the same dashboard raises `DashboardBusy`; `open` leaves the charts due; a schedule skips it. |

Each refresh publishes `dashboard.refreshed` (the counts per status; the trigger is `open`, `manual` or
`schedule`). Each chart is a run, with its own `run.started` and `run.finished`.

## One access rule

A datasource's stored connection sets the most anyone can see through it, and a person's roles and policies
narrow that. Every way to data uses the same path and the same checks: a question, a refresh, an `open` and
a schedule all go through `run_query` with the caller's `UserContext` (a schedule's is its owner's saved
snapshot). So losing access to a table removes its rows from every dashboard at the next `open`, and stored
results are never served before the check passes.

## Schedules

`set_schedule(id, every_minutes)` re-runs a dashboard by itself. It runs **only inside a long-running
process**: `nl2sql-api` starts a runner in its lifespan that calls `engine.dashboards.run_scheduled()` every
minute (a local app does the same, or cron runs `nl2sql dashboards refresh --due`). With nothing running,
nothing refreshes.

- **As its owner, as saved.** `set_schedule` saves your user, tenant and roles on the schedule. Each run
  resolves that snapshot against the *current* policies, so it can only narrow what you can read, never widen.
- **It stops, visibly.** The schedule becomes `stopped`, with a `code` and `message` in `schedule.error` (shown
  on the dashboard that `open` and `get` return), and `dashboard.schedule_stopped` is published (audited) when
  the saved identity is older than `dashboards.schedule_identity_max_age_days` (30,
  `SCHEDULE_IDENTITY_EXPIRED`), a role in it is gone from the policies (`SCHEDULE_ROLE_REMOVED`), a chart
  can no longer be read by its owner (`SCHEDULE_ACCESS_DENIED`) or a hook denied a chart (`SCHEDULE_HOOK_DENIED`,
  with the hook's reason). Nothing retries it. Setting the schedule again saves a fresh snapshot and starts it
  again.
- **A failure is not a stop.** A chart whose run fails (`error`) keeps its last good result and the schedule goes
  on.
- **It never approves.** A chart that needs a person's yes is skipped and keeps its last result; the schedule goes on.
- **One run at a time.** A dashboard another process is refreshing is skipped, so two runners (or a runner and
  cron) do not double up.

```bash
nl2sql dashboards refresh --dashboard sales --as analyst --user ana --tenant acme   # one dashboard, as that caller
nl2sql dashboards refresh --due                                                      # every schedule that is due
```

`--user` and `--tenant` act as that owner: the command is for the operator, who holds the config and the
credentials. `--dashboard` needs `--user`, since a dashboard belongs to a user. `--due` runs each schedule as its saved owner, so it refuses `--as`, `--user` and `--tenant`.

## The starter dashboard

`generate` makes a first dashboard from a datasource's schema, with no model and no profile run: it needs an
index.

```python
board, run = engine.dashboards.generate("sales", user_context=me)          # saved in your "General" workspace
engine.dashboards.suggest_questions("sales", user_context=me, n=5)         # the first five questions, nothing run
```

**What it reads.** Each column gets a role from the indexed snapshot alone (no query): what the schema proves (a
key is an *identifier*, a date type a *date*), then Wave B's name and statistics rules (`money`, `quantity`,
`enum`, a date held as text), and a role in `glossary.yaml` for that column wins over both. The dashboard reads
them as a **measure** (`money`, `quantity`, on a number column), a **time** (`date`), a **dimension** (`enum`, or a
text column with 2 to `charts.max_categories` (20) distinct values) or an **identifier**, which is never
charted. A rule's guess only picks a chart; it is never sent to a model. Only the tables **you** can read are
looked at: a table your roles deny is never charted or run.

**What it builds.** Four templates fill typed plans in code (the questions use the columns' names, humanised):

| Group | Question | Chart | At most |
|---|---|---|---|
| Key totals | "How many rows are in orders?"; "What is the total revenue?" | big number | 4 |
| Trends | "What is the total revenue by month over the last 12 months of data?" | line | 2 |
| Top N | "Which 10 customers have the highest total revenue?" | bar | 2 |
| Distributions | "How many orders rows are there per status?" (at most 12 values) | bar | 2 |

Tables rank by row count, then by how many measures they have; measures by their non-null share; dimensions by
distinct count (fewer first); ties break by name. So **the same snapshot and glossary always give the same
dashboard**. With no measure there are only row counts and distributions. The size is `max_tiles` (else
`dashboards.max_tiles`, 10).

**What it keeps.** Each chart is run as you, through the same path as any question (the `plan.after` hooks, the
validator, the generator, the `execute.before` hooks such as the cost guard), with no model call. The ones whose
first run is `ok` are kept; the rest are listed in `run.skipped` with their status and the reason, and leave no
stored result. If none answered, `NoTiles` says why and nothing is saved. A datasource that was never indexed
raises `NotIndexed` ("Index it first: `nl2sql index --datasource sales`"), and so does one you cannot reach
(the same error, so it tells you nothing about it); a caller who reaches the datasource but no table gets `NoTiles`.
If no chart is kept, a "General" workspace this call made is removed again. `suggest_questions` needs `n` of at
least 1.

**Where it lands.** The dashboard is saved, owned by you, in `workspace_id` or your "General" workspace (made on
first use), titled "`<datasource>` overview" unless you pass `title`. It has a `between` filter on the time column
the trends use and an `in` filter on each dimension the distributions use (their values are applied by refresh,
D2b). Layout follows the charts that were drawn: big numbers first, one column wide on one row; then lines (the
first four wide); then bars (two wide); tables last. It is an ordinary dashboard: rename, rearrange, remove, set
a schedule, refresh, all by hand, exactly as for one built in chat.

Each call publishes `dashboard.generated` (the counts, no titles) and the first run publishes
`dashboard.refreshed` with the trigger `generate`.

## Configuration

`dashboards:` in `nl2sql.yaml` (`max_tiles`, `min_refresh_minutes`, `schedule_identity_max_age_days`) is
accepted; `max_tiles` is read by `generate` and `suggest_questions`, `min_refresh_minutes` by `set_schedule` and
`schedule_identity_max_age_days` by the scheduler.