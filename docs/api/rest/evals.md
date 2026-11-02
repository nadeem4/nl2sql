# Evals API

Source: `packages/api/src/nl2sql_api/routes/evals.py`, `routes/jobs.py`

Both routes below are admin only (see [Admin routes](index.md#admin-routes)): a report holds every role's cases, plans
and SQL. Running the suites is a job, `POST /api/v1/jobs/eval`.

## Endpoints

### `GET /api/v1/evals/suites`

The suites `evals.suites` names, in order: `{"name", "path", "cases", "approved"}`, where `path` is as configured,
`cases` is the number in the file and `approved` the number that run. An empty list when none is configured.

### `GET /api/v1/evals/scorers`

The registered scorer names: the built-ins and every plugin's, as a list of strings.

### `POST /api/v1/jobs/eval`

Starts an eval run as a [job](jobs.md). Body (all optional):

| Field | Meaning |
|---|---|
| `suites` | Suites to run, each by its file or its name as `evals.suites` lists it (`GET /evals/suites`). Every configured suite when left out. A name that is not configured is a `422`: a caller never names a file. |
| `mode` | `full` (default), `plans` or `retrieval`. `plans` and `retrieval` call no model. |
| `cases` | Case ids or tags to run. |
| `max_cost_usd` | The cost cap for `full`. |

`full` calls the server's model, never a caller's, so it needs a cap: `max_cost_usd`, else `evals.max_cost_usd`; with
neither it is a `422`. Answers `202` with the queued `Job`; progress is one step per case (`case <id>`); the `result` is
an `EvalReport`.
