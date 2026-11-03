# REST API routes

The `nl2sql-api` package serves the engine over HTTP with FastAPI. At startup
it loads the `.env` next to `nl2sql.yaml` (never overriding a variable already
set), builds one engine with `NL2SQL.from_config()` (`NL2SQL_CONFIG`, else
`./nl2sql.yaml`) and calls `engine.check()` before it accepts a request: a
`required` datasource that cannot connect stops it from starting.

```bash
pip install nl2sql-api
cd nl2sql-demo                               # ./nl2sql.yaml is the config
nl2sql-api serve --port 8000        # 127.0.0.1, one worker
# or: NL2SQL_CONFIG=nl2sql-demo/nl2sql.yaml uvicorn nl2sql_api.main:app --port 8000
```

Its own settings are the `api` section of `nl2sql.yaml`, which the engine
ignores:

```yaml
# api.yaml (included from nl2sql.yaml as  api: { file: api.yaml })
auth: static                    # api_keys | proxy | static | none (default none)
static_role: admin              # one static role for every request
cors_origins: []                # browser origins allowed to call the API
```

Full interactive docs at `http://localhost:8000/docs` (Swagger UI), with ReDoc
at `/redoc` and the schema at `/openapi.json`. Browser access from other origins
is off unless listed in `api.cors_origins`.

`api.auth` picks who is asking, and the request body never carries the caller:

| `api.auth` | Identity |
| --- | --- |
| `api_keys` | `Authorization: Bearer <key>` from `nl2sql keys create`. |
| `proxy` | The user, tenant and roles headers of a proxy that connects from `api.proxy.trusted_from`. |
| `static` | `api.static_role` for every request. |
| `none` (default) | The local operator, for a request made straight to `localhost` only; `nl2sql-api serve` refuses to bind any other host. |

With no identity the routes answer HTTP 401 (HTTP 403 under `none` for a request that is not the local operator). Every
error is one envelope, `{"error": {"code", "message", "fix", "details", "request_id"}}`.

The routes that change the process (`POST /api/v1/datasources`, `DELETE /api/v1/datasources/{id}`, and the
Connect routes `GET /api/v1/datasource-types` and `POST /api/v1/datasources/test`,
`PUT` and `PATCH /api/v1/settings/llm`, the `/api/v1/api-keys` and `/api/v1/evals` routes, `POST /api/v1/jobs/index`,
`POST /api/v1/jobs/profile`, `POST /api/v1/jobs/eval`, `POST /api/v1/jobs/demo`, `DELETE /api/v1/index`,
`GET /api/v1/index/check`, `POST /api/v1/knowledge/reload` and every `/proposals` route)
also need a role listed in `policies.admin_roles` (default `[admin]`), else HTTP 403.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/query` | Ask a question; returns the plan, checks, SQL, a row sample, the result manifest, answer, errors and usage |
| `POST` | `/api/v1/query/stream` | The same run as Server-Sent Events: each step as it starts and ends, the result manifest, the answer as it is written, then the `/query` response |
| `GET` | `/api/v1/results/{trace_id}/{result_id}` | One page of a run's saved result (`offset`, `limit` up to 1,000) |
| `GET` | `/api/v1/results/{trace_id}/{result_id}.csv` | The whole of a run's saved result as CSV, streamed |
| `GET` | `/api/v1/results/{trace_id}` | The run's saved results: columns, row counts, never rows |
| `GET` | `/api/v1/results/{trace_id}/{result_id}/chart` | The chart a result is drawn as (`?format=vega-lite` for a Vega-Lite v5 spec) |
| `GET` | `/api/v1/traces/{trace_id}` | The trace of a run the caller asked |
| `POST` | `/api/v1/feedback` | Rate a run the caller asked, up or down |
| `POST` | `/api/v1/feedback/correct` | Turn the caller's fixed SQL or plan into a proposal for a verified query |
| `GET` | `/api/v1/feedback` | Every rating, newest first (admin) |
| `GET` | `/api/v1/approvals` | Approval requests the caller asked or may decide (`status`) |
| `POST` | `/api/v1/approvals/{request_id}/grant` | Grant a request (an approver role) |
| `POST` | `/api/v1/approvals/{request_id}/deny` | Deny a request, with an optional reason |
| `POST` | `/api/v1/conversations` | Start a conversation |
| `GET` | `/api/v1/conversations` | The caller's conversations, newest first |
| `GET` | `/api/v1/conversations/{conversation_id}` | One conversation with its turns |
| `DELETE` | `/api/v1/conversations/{conversation_id}` | Delete a conversation |
| `POST` | `/api/v1/conversations/{conversation_id}/turns` | Send a message (text or a choice); returns the stored turn |
| `POST` | `/api/v1/conversations/{conversation_id}/turns/stream` | The same turn as Server-Sent Events, ending with the stored turn or a `run.failed` frame |
| `POST` | `/api/v1/conversations/{conversation_id}/retry` | Run the last question again, with approved request ids |
| `POST` | `/api/v1/conversations/{conversation_id}/tools/{name}` | Run one chat tool with its arguments |
| `GET` | `/api/v1/conversations/{conversation_id}/report` | The conversation as a Markdown report |
| `GET` | `/api/v1/chat-tools` | The chat tools a conversation can use |
| `GET` | `/api/v1/health` | Liveness check |
| `GET` | `/api/v1/ready` | Readiness from the engine's check, kept 10 s: `200 ready` or `503 not_ready` (an admin also gets `problems`) |
| `GET` | `/api/v1/meta` | The mode, the identity source and whether callers may send their own LLM; needs no caller. With a caller: the datasources, which questions are recorded, and `recorded_with` |
| `GET` | `/api/v1/about` | What the engine is: version and pipeline steps |
| `GET` | `/api/v1/events/catalog` | Every event the engine publishes, with its data's JSON Schema |
| `GET` | `/api/v1/llm/providers` | The LLM providers: key present, verified models, the shape a key must have |
| `GET` | `/api/v1/api-keys` | List API keys, revoked ones included, never a token (admin) |
| `POST` | `/api/v1/api-keys` | Create a key for a user and roles; the token is returned once (admin, `201`) |
| `DELETE` | `/api/v1/api-keys/{key_id}` | Revoke a key (admin, `204`) |
| `GET` | `/api/v1/evals/suites` | The configured eval suites and their case counts (admin) |
| `GET` | `/api/v1/evals/scorers` | The registered scorer names (admin) |
| `POST` | `/api/v1/jobs/eval` | Start an eval run (admin, `202`): `suites`, `mode`, `cases`; `full` needs a cost cap |
| `POST` | `/api/v1/jobs/demo` | Start the demo job (admin, `202`): add the sample databases and index them |
| `GET` | `/api/v1/jobs` | The caller's background jobs, newest first (an admin sees all, without their results) |
| `GET` | `/api/v1/jobs/{job_id}` | One job: status, progress, result or error |
| `DELETE` | `/api/v1/jobs/{job_id}` | Cancel a queued or running job |
| `GET` | `/api/v1/jobs/{job_id}/events` | A job's progress as Server-Sent Events, ending with `job.finished` |
| `POST` | `/api/v1/jobs/dashboard` | Queue a job that builds the no-model starter dashboard for a datasource (`202`) |
| `POST` | `/api/v1/jobs/dashboard-build` | Queue a job that builds a dashboard from the guided builder's ticked tiles (`202`) |
| `GET` | `/api/v1/dashboard-builder/options` | The guided builder's chips for a datasource: curated queries, saved answers, metrics, dimensions, time ranges |
| `POST` | `/api/v1/dashboard-builder/proposals` | The tiles to tick, from the chosen sources and chips; only `asked` calls a model |
| `POST` | `/api/v1/answers` | Save the answer of a chat step as the caller's own, and propose it for review |
| `GET` | `/api/v1/answers` | The caller's saved answers, optionally those of one datasource |
| `GET` | `/api/v1/answers/{answer_id}` | One saved answer (another caller's is a `404`) |
| `DELETE` | `/api/v1/answers/{answer_id}` | Delete a saved answer (`204`) |
| `POST` | `/api/v1/answers/{answer_id}/run` | Run a saved answer's plan again as the caller; no model call |
| `GET` | `/api/v1/dashboards` | The caller's dashboards, saved or draft, optionally in one workspace |
| `GET` | `/api/v1/dashboards/{dashboard_id}` | A dashboard and the stored results of its charts; reads only, runs no query |
| `DELETE` | `/api/v1/dashboards/{dashboard_id}` | Delete a dashboard, its stored results and its schedule |
| `POST` | `/api/v1/dashboards/{dashboard_id}/refresh` | Re-run the stored plans of one chart or all of them as the caller; no model call |
| `GET` | `/api/v1/dashboards/{dashboard_id}/filters/{filter_id}/values` | The values a filter offers, as the caller may read them |
| `PUT` | `/api/v1/dashboards/{dashboard_id}/title` | Rename a dashboard |
| `PUT` | `/api/v1/dashboards/{dashboard_id}/tiles/{tile_id}/title` | Rename a chart |
| `PUT` | `/api/v1/dashboards/{dashboard_id}/tiles/{tile_id}/chart` | Choose how a chart is drawn |
| `DELETE` | `/api/v1/dashboards/{dashboard_id}/tiles/{tile_id}` | Remove a chart and its stored result |
| `PUT` | `/api/v1/dashboards/{dashboard_id}/layout` | Place the charts on the 4-column grid |
| `PUT` | `/api/v1/dashboards/{dashboard_id}/filters` | Store the filter definitions |
| `PUT` | `/api/v1/dashboards/{dashboard_id}/schedule` | Set or stop the refresh schedule |
| `POST` | `/api/v1/dashboards/{dashboard_id}/pin` | Save a draft whole into a workspace |
| `POST` | `/api/v1/dashboards/{dashboard_id}/move` | Move a dashboard to another workspace |
| `GET` | `/api/v1/datasources/{datasource_id}/starter-questions` | The questions of the charts the starter dashboard would build; nothing runs |
| `GET` | `/api/v1/workspaces` | The caller's workspaces, oldest first |
| `POST` | `/api/v1/workspaces` | Create a workspace (`201`) |
| `PUT` | `/api/v1/workspaces/{workspace_id}/name` | Rename a workspace |
| `DELETE` | `/api/v1/workspaces/{workspace_id}` | Delete an empty workspace (`204`) |
| `GET` | `/api/v1/datasource-types` | The adapters the engine can name, installed or not, each with its connection schema (the Connect a database form) |
| `POST` | `/api/v1/datasources/test` | Connect to a datasource that is not added and report the stage that failed and the fix; registers nothing |
| `POST` | `/api/v1/datasources` | Register a datasource in the running process; `config` has the shape of one `datasources` entry (`id`, `type`, `connection`, ...). `save: true` also writes `datasources/<id>.yaml`, with every secret as a reference; a read-only config answers `403 NOT_WRITABLE` and keeps it registered until restart. A body that does not validate is a 422 listing each problem by path |
| `GET` | `/api/v1/datasources` | List the datasources the caller reaches: type, description, status (`ready`, `failed`, `unchecked`), capabilities, masked connection, sample questions |
| `GET` | `/api/v1/datasources/{datasource_id}` | One datasource, as the list shows it |
| `DELETE` | `/api/v1/datasources/{datasource_id}` | Take a datasource out of the running process |
| `GET` | `/api/v1/datasources/{datasource_id}/schema` | The tables and columns the caller may see |
| `GET` | `/api/v1/datasources/{datasource_id}/diagram` | The schema as a Mermaid `erDiagram` (plain text); repeat `tables` to draw some and their neighbours |
| `GET` | `/api/v1/index` | Index health: status, entries by type, embedding model and problems per datasource |
| `DELETE` | `/api/v1/index` | Clear the vector store |
| `GET` | `/api/v1/index/check` | Drift check: compare each datasource's live schema with what was indexed (admin) |
| `GET` | `/api/v1/index/inspect` | The retrieval inspector's defaults |
| `POST` | `/api/v1/index/inspect` | Run a retrieval and show the pool, the picks and what was dropped |
| `POST` | `/api/v1/jobs/index` | Start the index job (admin, `202`): index every datasource or `datasource_ids`; progress per datasource |
| `POST` | `/api/v1/jobs/profile` | Start the profile job (admin, `202`): profile datasources into proposals, on the server's key; `draft`, `max_cost_usd` |
| `GET` | `/api/v1/knowledge` | The merged knowledge per datasource, with where each item came from and its problems |
| `GET` | `/api/v1/knowledge/sources` | Each knowledge source, its counts, checksum, load time and last error |
| `POST` | `/api/v1/knowledge/reload` | Reload every knowledge source and swap atomically (admin) |
| `POST` | `/api/v1/knowledge/choices` | File a clarification pick as a `term` proposal |
| `GET` | `/api/v1/proposals` | List proposals by `kind`, `status`, `datasource_id` and `limit` (admin) |
| `GET` | `/api/v1/proposals/{proposal_id}` | One proposal (admin) |
| `POST` | `/api/v1/proposals/accept` | Accept proposals by id, with optional edits; writes each file once (admin) |
| `POST` | `/api/v1/proposals/reject` | Reject proposals by id, with a reason (admin) |
| `POST` | `/api/v1/proposals/examples` | File starter questions as `example` proposals (admin) |
| `PUT` | `/api/v1/settings/llm` | Replace the whole `llm` section in the running process (the body is the section as JSON; a bad one is a 422) |
| `PATCH` | `/api/v1/settings/llm` | Change only the provider and model of named agents; every other `llm` setting stays (admin) |
| `GET` | `/api/v1/llm` | Each agent and the model it runs on |

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"question": "How many customers are there?"}'
```

The response has `status`, `sub_queries`, `final_answer`, `errors`, `warnings`,
`reasoning`, `timings`, `usage`, `artifact_refs`, `trace_id` and `trace_path`.
Each sub-query carries `id`, `intent`, `datasource_id`, `schema_version`,
`plan`, `validation`, `sql`, `rows`, `status`, `retry_count` and `plan_source`.
`rows` is a capped sample with the true total; the full result set lives in
artifact storage, addressed by `artifact_refs`. A pipeline failure, a refusal
included, comes back as HTTP 200 with `status: "error"` and the reason in
`errors`. Per-area reference: [REST API](index.md).
