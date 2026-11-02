# Saved answers API

## Purpose
`engine.answers`: a person's own record of "this answered my question". It stores the typed plan behind an
answer, not SQL and not rows, and runs it again on request. Design: [Wave D, section 18](../../architecture/wave_d.md).

## Key Modules
- `packages/nl2sql/src/nl2sql/api/answers_api.py` (`AnswersAPI`, internal)
- `packages/nl2sql/src/nl2sql/answers/` `models.py` (`SavedAnswer`, `SaveReport`), `store.py` (the owner check),
  `service.py` (the logic)

## Public Surface

Every method takes `user_context` keyword-only and required. A missing caller, or one with no `user_id`, is a
`TypeError`, not an anonymous owner.

| Method | Returns | Notes |
| --- | --- | --- |
| `save(*, trace_id, conversation_id, step, user_context, note=None)` | `SaveReport` | Keeps the answer of an `ask` step of the caller's conversation and rates its run up. |
| `list(*, user_context, datasource_id=None)` | `List[SavedAnswer]` | Newest first. |
| `get(id, *, user_context)` | `SavedAnswer` | |
| `delete(id, *, user_context)` | `None` | |
| `run(id, *, user_context, llm_override=None, approvals=None)` | `QueryResult` | Runs the plan again as the caller, with no model call (`answer=False`, as a tile), so it works with no key. |

`SavedAnswer` has `id`, `tenant_id`, `user_id`, `question`, `datasource_id`, `plan` (a `ChartPlan`, as a dashboard
tile holds), `chart` (`auto` by default), `created_at` and `trace_id`. `SaveReport` has `answer`, `proposed`,
`proposal_id` and `not_proposed_reason`.

## Behaviour

- **Owner only.** An answer is read, run, deleted and listed only by its owner. Another caller, an admin
  included, gets `AnswerNotFound` (`ANSWER_NOT_FOUND`, 404) (D247).
- **`save` takes the plan from the stored step**, the helper `AddTile` uses, so no model text becomes a plan. The
  step must have answered and kept a plan, and `trace_id` must be that step's run: otherwise `NotSavable`
  (`NOT_SAVABLE`, 422). A conversation that is not the caller's is `ConversationNotFound`.
- **It rates the run up** through `engine.feedback`, which also files the run's `verified_query` proposal when it
  can (origin `thumbs_up`). The answer is saved either way (D241): `proposed` is `False` and
  `not_proposed_reason` says why (`verified`, `multiple_statements`, `failed`, `no_sql`, `no_trace` when the run's
  trace was not kept, and the other reasons `skip_reason` gives). `note` is stored on the rating.
- **A cross-database answer is filed under its first sub-query's datasource**, so `list(datasource_id=...)` for its other datasources does not return it.
- **The cap never overflows.** `save` counts and stores under the owner's lock; a save that meets another in flight raises `LockHeld` and can be retried. A verified answer whose source names no datasource is `NotSavable`.
- **Saving the same run twice** is one answer and one proposal.
- **The cap.** At most `answers.max_per_user` answers (default 100) per person. The next save is refused with
  `AnswerLimitReached` (`ANSWER_LIMIT_REACHED`, 409) before anything is rated; deleting one frees a place (D242).
- **`run` is a normal run as the viewer** (D240): the plan goes through `plan.after`, the validator, the generator,
  the scope and the hooks, as for any run, and `approvals` are as `run_query` takes them. A plan the schema has
  outgrown comes back as a result with status `error` whose first error has `error_code` `TILE_NEEDS_FIXING`, as a
  dashboard tile reports it. The answer is kept.
- **Storage.** `StateStore` version 6 ([The state store](../../schema/store.md#saved-answers)).
