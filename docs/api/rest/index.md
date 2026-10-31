# REST API (FastAPI)

This section documents the `nl2sql-api` FastAPI package that exposes the core
engine over HTTP. Implementation lives in `packages/api/src/nl2sql_api/`.

## Runtime Model

### Application lifecycle

Source: `packages/api/src/nl2sql_api/main.py`

- `lifespan` configures logging, loads the `.env` next to `nl2sql.yaml` (never
  overriding a variable already set), builds one engine with
  `NL2SQL.from_config()` (the file `NL2SQL_CONFIG` names, else `./nl2sql.yaml`)
  and calls `engine.check()` before it serves. A `required` datasource that
  fails, or no datasource ready at all, raises `EngineNotReady` and the server
  does not start; an optional one that fails leaves it serving the others. The
  engine is stored in `app.state.engine` and closed at shutdown. Logs are
  text, or JSON lines with `NL2SQL_LOG_FORMAT=json`.
- Retention: the lifespan calls `engine.storage.purge()` once before it
  serves and then once a day (`purge_daily`, every `PURGE_INTERVAL_S`
  seconds), so traces and results older than their store's `max_age_days`
  are deleted (see [Storage → Retention](../../configuration/storage.md#retention)).
  A failed purge is logged and never stops the server.
- Dashboard schedules: the lifespan starts a runner that calls
  `engine.dashboards.run_scheduled()` every minute (`run_schedules`, every
  `SCHEDULE_INTERVAL_S` seconds), so a dashboard with a schedule refreshes as
  its owner while the server runs (see [Dashboards](../../dashboards/index.md#schedules)).
  A failed run is logged and never stops the server.
- The package imports the engine only through the top-level `nl2sql` namespace
  (`from nl2sql import NL2SQL, QueryResult, UserContext, configure_logging, ...`),
  never an engine submodule. `packages/api/tests/test_architecture.py` enforces it.
- `create_app(config_path=None)` builds the app: one router module per namespace (`health`, `query`, `approvals`,
  `conversations`, `dashboards`, `workspaces`, `datasources`, `datasource`, `index`, `indexing`, `knowledge`, `evals`,
  `settings`, `llm`, `web`) under `/api/v1` (`web` at the root), and `errors.install` for the
  [error envelope](errors.md). `nl2sql_api.main:app` is `create_app()` for `uvicorn nl2sql_api.main:app`.
- CORS is restricted to the origins in `api.cors_origins`
  (see [CORS origins](#cors-origins)); with none configured, no cross-origin
  browser access is allowed.

### Dependency injection

Source: `packages/api/src/nl2sql_api/dependencies.py`

- `get_engine(request)` returns the singleton `NL2SQL` instance.
- Service providers (`DatasourceService`, `QueryService`, `LLMService`,
  `IndexingService`, `HealthService`) are created per-request with the engine.
- `identity.caller` (in `nl2sql_api/identity.py`) returns the caller's
  `UserContext`; see [Identity](#identity).
- `identity.admin` guards the routes that change datasources, LLMs or the
  index; see [Admin routes](#admin-routes).

## Interactive docs

FastAPI serves Swagger UI at `/docs`, ReDoc at `/redoc` and the OpenAPI schema at
`/openapi.json`, with a summary, description and tag on every route. Start the
server on a config:

```bash
nl2sql-api serve --config nl2sql-demo/nl2sql.yaml --port 8000   # one worker, 127.0.0.1; then open http://localhost:8000/docs
```

## API Index

| API | Router Path | Purpose |
| --- | --- | --- |
| [Health](health.md) | `routes/health.py` | Liveness (`/health`), readiness (`/ready`), `/meta` (mode, auth source, `request_llm`; no caller) and `/about`. |
| [Query](query.md) | `routes/query.py` | Execute natural language queries, whole or streamed as Server-Sent Events; page through or download a run's saved result. |
| [Datasource](datasource.md) | `routes/datasource.py` | Manage datasource configs. |
| [LLM](llm.md) | `routes/llm.py` | Replace the LLM (`PUT /settings/llm`) and inspect each agent's model. |
| [Indexing](indexing.md) | `routes/indexing.py` | Index management and status. |
| [Errors](errors.md) | `errors.py` | The one error envelope and the request id. |

The routers `approvals`, `conversations`, `dashboards`, `workspaces`, `datasources`, `index`, `knowledge`, `evals`, `settings` and `web` are empty
for now: later rows fill them. The committed contract is `packages/api/openapi.json`; `test_openapi_snapshot_is_current`
fails when a route changes without it (rewrite it with `python packages/api/tests/test_openapi_snapshot.py`).

## Error Handling

Every non-2xx response is the [error envelope](errors.md),
`{"error": {"code", "message", "fix", "details", "request_id"}}`. Only the facade's own `NL2SQLError` (and its named
subclasses) is mapped to its status; anything else, a built-in `ValueError`, `KeyError`, `PermissionError` or a
pydantic `ValidationError` included, is a `500` that names a request id and never the exception's text. Routes carry
no `except Exception`.

`POST /api/v1/query` still returns pipeline errors as a normal `HTTP 200` inside `QueryResponse.errors`.

Route handlers that call blocking engine code are declared with `def` rather than
`async def`, so Starlette runs them in its threadpool instead of blocking the
event loop.

## Configuration

The REST API is configured by the same `nl2sql.yaml` as the engine. Beyond the
runtime server options of `nl2sql-api serve` (`--config`, `--host`, `--port`; one worker, no reload),
its own settings are the `api` section of that file (see
[The `api` section](../../configuration/api.md)), and the admin roles are
`policies.admin_roles`. The engine validates the `api` section and never reads it.

### CORS origins

`api.cors_origins` lists the browser origins allowed to call the API
cross-origin. Each entry is a full origin (scheme, host and, if non-default,
port).

```yaml
# api.yaml
cors_origins: [https://bi.corp.example, http://localhost:3000]
```

With one or more origins configured, only those origins are accepted and
credentialed requests (cookies, `Authorization`) are permitted.

With none, no origins are allowed and credentials are disabled. That is the
intended default, not a misconfiguration: a browser page served from another
origin cannot call the API until you opt it in. The list is read from the
engine's config, which is loaded at startup, so changing it requires
restarting the server.

This setting governs the browser same-origin policy only. Non-browser clients --
`curl`, server-side callers, the Python SDK -- do not send an `Origin` header and
are unaffected by it; use authentication and network controls to restrict those.

### Identity

The engine enforces RBAC; the API decides who is asking. `api.auth` picks one source, and every request resolves one
`UserContext` through the `identity.caller` dependency. The request body is never read for it.

| `api.auth` | Identity | No identity |
| --- | --- | --- |
| `api_keys` | `Authorization: Bearer <key>`, verified by `engine.api_keys.verify`: the key's user, tenant and roles. | `401` with `WWW-Authenticate: Bearer` |
| `proxy` | The `api.proxy.user_header`, `tenant_header` (else `settings.tenant_id`) and `roles_header` (comma-separated), honoured only from a peer in `api.proxy.trusted_from`. | `401` (also for a peer outside `trusted_from`, whose headers are ignored) |
| `static` | One role, `api.static_role`, for every request, with no user id. | `401` when `static_role` is unset |
| `none` (default) | The local operator: `user_id: "local"` with the `policies.admin_roles`. | `403` unless the request is made straight to localhost |

Under `none`, `nl2sql-api serve` refuses to bind a host that is not loopback (D205), and a request whose `Host` is not
`localhost`, `127.0.0.1` or `[::1]`, or that carries `X-Forwarded-For`, `Forwarded` or `X-Real-IP`, is a `403`, so a
reverse proxy on the same host or a DNS-rebinding page is not taken for the operator.

```yaml
# api.yaml behind an authenticating proxy at 10.0.0.5.
auth: proxy
proxy: { trusted_from: [10.0.0.5/32] }
```

A test or an embedding app overrides `identity.caller` and `identity.admin` with `app.dependency_overrides`.

### Admin routes

The routes that change the running process need an admin role, checked by the
`identity.admin` dependency:

- `POST /api/v1/datasource` and `DELETE /api/v1/datasource/{datasource_id}`
- The Connect routes `GET /api/v1/datasource-types` and `POST /api/v1/datasource/test`
- `PUT /api/v1/settings/llm`
- `POST /api/v1/index/{datasource_id}`, `POST /api/v1/index-all` and `DELETE /api/v1/index`

| Key | Default | Meaning |
| --- | --- | --- |
| `policies.admin_roles` | `[admin]` | The roles that may call the admin routes. |

A caller whose roles include one in `policies.admin_roles` is let through; one with
roles but none of them admin is `HTTP 403`; one with no identity is `HTTP 401`. The check
uses `admin_roles` alone, so `policies.allow_all` does not make a caller an admin.

The read-only routes (`/health`, `/ready`, `/meta`, `GET /datasource`, `GET /llm`, which
never returns API keys, and `GET /index/status`) need no role.

```yaml
# The proxy sets X-NL2SQL-Roles; only "ops" may change datasources and the index.
# api.yaml
auth: proxy
# policies.yaml (with the roles)
admin_roles: [ops]
```
