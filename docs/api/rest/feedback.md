# Traces and feedback API

A run's trace, a rating of its answer, and a correction that becomes a proposal for a verified query. All three act on a
run the caller asked: another caller's run is a `404`, and `feedback/correct` is always checked as the caller (the
route never takes the trusted in-process operator path the CLI has). Source:
`packages/api/src/nl2sql_api/routes/query.py`.

### `GET /api/v1/traces/{trace_id}`

The run's trace document: its request, every node's input and output, and the result. An unknown trace, another
caller's, or an id that is not a plain token is a `404`. The operator's `settings` block is left out, and
`llm.configured` keeps only each agent's provider, model and temperature (no endpoint or header names). When a hook
withheld the run's results (`answer.after` denied them, or they were never released) the trace also has none of their
rows (`rows`, `terminal_results`) and none of its LLM calls' prompts or answers, which carry preview rows; the asker
reads those rows only through the results routes, which refuse such a run.

### `POST /api/v1/feedback`

Body `{"trace_id", "rating": "up" | "down", "note"?}`. The question, SQL and answer are read from the run's trace, never
the body. Returns the stored rating. An unknown or another caller's run is a `404`; a bad rating, a `trace_id` that is
not a plain token (letters, digits, `-`, `_`), or an unknown body field, is a `422`.

### `POST /api/v1/feedback/correct`

Body `{"trace_id", "sql" | "plan", "question"?, "note"?}` with exactly one of `sql` and `plan`. Returns a `Proposal` for
a verified query (`origin: "correction"`); nothing is saved until a reviewer accepts it.

| status | code | when |
| --- | --- | --- |
| 404 | `NOT_FOUND` | no such run for this caller |
| 422 | `INVALID_REQUEST` | a `trace_id` that is not a plain token, or not exactly one of `sql` and `plan` |
| 422 | `CORRECTION_REFUSED` | the fix is not read-only, reads what the caller may not, fails validation, or a hook denies it; `details` is one `{code, msg}` per reason |

### `GET /api/v1/feedback`

Every rating, newest first (`limit` caps it). Admin only: it spans all callers.

## Tests

`packages/api/tests/test_feedback_routes.py`, over the real facade with a run of one caller read by another: the trace,
the rating, the correction and its refusals, that `as_operator` is never passed, and that another caller's run can
neither be rated nor corrected.
