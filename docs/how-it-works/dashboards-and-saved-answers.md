# Dashboards and Saved Answers

How a dashboard keeps its charts, how it refreshes without a model, and how saved answers feed it, for contributors and operators.

Using them is covered in [Dashboards](../dashboards/index.md); the methods are in [Dashboards API](../api/core/dashboards.md) and [Saved answers API](../api/core/answers.md).

## A dashboard holds plans, not SQL or rows

A `Dashboard` (`packages/nl2sql/src/nl2sql/dashboards/models.py`) has a title, a workspace, tiles, filters and an optional schedule. Each tile holds the typed plan that produced its chart (or, for a verified chart, the verified query id and its values). The last result of each tile is a separate record with a timestamp, capped at `charts.max_points` rows.

Because a tile keeps a plan, a dashboard can be re-run later by anyone with access, on a changed schema, without asking the model again.

## Whole dashboards, in workspaces

A dashboard built in a conversation is a **draft** tied to that conversation. `pin` saves the whole dashboard into a workspace: a chart is never pinned alone. Drafts are purged after `conversations.max_age_days`; saved dashboards are never purged. A workspace is a private, named folder, and a dashboard lives in exactly one. Every change a person makes (title, layout, filters, schedule, a chart's kind) is a typed edit in `dashboards/edits.py` that calls no model.

## Refresh costs no tokens

Each tile re-runs as `run_query(question, plan=SuppliedPlan(...), answer=False, user_context=caller)`:

- the plan goes through the `plan.after` hooks, the validator and the generator under the caller's scope;
- it is not cached, not sent to the refiner, and no answer is written, so no model is called;
- a plan that no longer validates (a column was dropped) becomes `needs_fixing` with the validator's message; the other tiles keep working, and fixing it is a conversation turn;
- a failed run does not replace the tile's last good result.

`open` serves stored results only after re-checking every stored plan under the caller's current roles. A refresh holds the lock `dashboard-refresh:<id>`, so two refreshes of one dashboard never overlap.

## One access rule

A datasource's stored connection sets the most anyone can see through it, and a person's roles and policies narrow that. A question, a refresh, an `open` and a schedule all go through `run_query` with a `UserContext`. A schedule's is its owner's saved snapshot, resolved against the current policies at each run, so it can only narrow what the owner may read. Losing access to a table removes its rows from every dashboard at the next `open`.

## Schedules

`set_schedule` re-runs a dashboard by itself, but only inside a long-running process: `nl2sql-api` runs `engine.dashboards.run_scheduled()` every minute, and `nl2sql dashboards refresh --due` does the same from cron. A schedule stops, visibly, when its saved identity is too old, a role is gone, a chart can no longer be read or a hook denied it. It never approves a request that needs a person's yes.

## Saved answers

"Yes, this answered it" under a chat answer saves a **saved answer**: the question, the datasource and a `ChartPlan` (the same shape a tile holds), owned by the person who saved it (`packages/nl2sql/src/nl2sql/answers/`). The click also files the existing thumbs-up proposal for the review queue, when a verified query can be made from the run.

- A saved answer runs again as the viewer, every time, through the same path as a tile.
- It is owner-only. There is no admin read, and a tile built from one carries a copy of the plan, so a shared dashboard never exposes another person's saved answer.
- `answers.max_per_user` (100) bounds the store; a full store refuses the next save by name rather than dropping one.

## The guided builder

The builder makes a dashboard from chosen sources without chat, in two steps (`dashboards/builder.py`):

1. `propose_tiles` is read-only and runs nothing. It offers three sources: curated verified queries, the person's saved answers, and "tell us what matters", where chips (metrics, breakdowns, periods) become at most three new questions for the model.
2. `build` adds the ticked tiles. Curated and saved tiles need no model; new questions run as `ask` steps of one builder conversation. Over REST this is a job.

Only the third source calls a model, so the first two work with no key.

## Why

Storing the plan makes refresh cheap, repeatable and safe to hand to someone else: the plan is checked again for each viewer, so the dashboard cannot widen anyone's access. See [Access and security](access-and-security.md) and [Plans and SQL](plans-and-sql.md).
