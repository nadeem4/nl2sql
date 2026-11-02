# Saved answers

A person's own record of "this answered my question": the typed plan behind a chat answer, run again on request. It is
the REST face of [`engine.answers`](../core/answers.md); design in [Wave D, section 18](../../architecture/wave_d.md).
Code: `packages/api/src/nl2sql_api/routes/answers.py`.

A saved answer belongs to its owner alone. Another caller's answer is a `404` `ANSWER_NOT_FOUND`, an admin's call
included, and no route acts as the in-process operator. On the hosted demo the owner is the visitor.

| Route | Does |
|---|---|
| `POST /api/v1/answers` | Body `{trace_id, conversation_id, step: {turn?, step}, note?}`. `200` with a `SaveReport`: `{answer, proposed, proposal_id, not_proposed_reason}`. The plan is read from the stored step. Saving the same run again returns the same answer. |
| `GET /api/v1/answers` | The caller's `SavedAnswer` list, newest first. Optional `datasource_id`. |
| `GET /api/v1/answers/{id}` | One `SavedAnswer`: `id`, `question`, `datasource_id`, `plan`, `chart`, `created_at`, `trace_id`. |
| `DELETE /api/v1/answers/{id}` | `204`. |
| `POST /api/v1/answers/{id}/run` | Optional body `{approval_ids}`. A `QueryResult`, run again as the caller (their data scope, hooks and approvals apply). No model call, so it works with no key. A plan the schema has outgrown is a result with `status: "error"` whose first error is coded `TILE_NEEDS_FIXING`. |

## Errors

| Status | Code | When |
|---|---|---|
| `404` | `ANSWER_NOT_FOUND` | No such answer, or not the caller's. |
| `409` | `ANSWER_LIMIT_REACHED` | The caller is at `answers.max_per_user` (default 100). |
| `422` | `NOT_SAVABLE` | The step did not answer, kept no plan, or is not the run named. |
| `422` | `INVALID_REQUEST` | A body that does not validate. |

See [Errors](errors.md) for the envelope.
