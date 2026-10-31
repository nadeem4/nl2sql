# Jobs API

Source: `packages/api/src/nl2sql_api/routes/jobs.py`, `jobs.py`, `sse.py`, `overrides.py`.

A job is one facade call (an index build, a profile, an eval, a dashboard) that outlives its request. One worker thread
in the server takes jobs in submission order, so two index builds never race on the index. The last 100 jobs are kept in
memory; a restart loses them, and what they produced (an index, proposals, a dashboard) stays in the engine's stores.
`nl2sql-api serve` therefore runs one Uvicorn worker and has no `--workers` option.

A caller with a job queued or running gets a `409` naming it when it starts another, so one caller cannot stall the
queue. Another user's job is a `404`; an admin sees every caller's.

## The `Job`

| Field | Holds |
|---|---|
| `id` | 22 url-safe characters |
| `kind` | `index`, `profile`, `eval`, `dashboard` or `demo` |
| `status` | `queued`, `running`, `succeeded`, `failed` or `cancelled` |
| `params` | The request body that started it, typed per kind |
| `created_by` | The caller's user id |
| `created_at`, `started_at`, `finished_at` | Timestamps |
| `progress` | `[{at, step, done, total}]`, one per item (a datasource, a case, a tile) |
| `result` | Typed per kind once `succeeded`: `IndexResult`, `ProfileReport[]`, `EvalReport` or `{dashboard, run}` |
| `error` | The error envelope's body once `failed`; never an exception's text |

## Endpoints

| Route | Does |
|---|---|
| `GET /api/v1/jobs` | The caller's jobs, newest first |
| `GET /api/v1/jobs/{job_id}` | One job |
| `DELETE /api/v1/jobs/{job_id}` | Cancel: a queued job is removed; a running one stops between items. Returns the job |
| `GET /api/v1/jobs/{job_id}/events` | Progress as Server-Sent Events |

The routes that start a job (`POST /api/v1/jobs/<kind>`) arrive with the rows that own each kind.

## Events

`GET /jobs/{id}/events` sends one `job.progress` event (a `JobProgress` as `data`) for every step so far, then for each
new one, then `job.finished` (the whole `Job`) and closes. A `: keep-alive` comment is sent every 15 seconds. The wire is
the one every stream uses:

```
event: job.progress
data: {"at": "...", "step": "chinook", "done": 1, "total": 3}

event: job.finished
data: {...the Job...}
```

The stream waits without holding a thread.

## Per-request keys and models

Where `api.request_llm: true`, a request may carry `X-NL2SQL-Api-Key-<provider>` (one provider's key per header) and
`X-NL2SQL-Models` (`{"ast_planner": "anthropic:claude-opus-5"}`, no secret). They become the engine's `LLMOverride`.
With `request_llm` off they are a `403`. A model on a provider the same request sent no key for is a `422`
`MISSING_PROVIDER_KEY`, even when the server holds that provider's key. A key is never logged or echoed.
