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
  engine is stored in `app.state.engine` and closed at shutdown. When
  `settings.observability.exporter` is `otlp`, logs switch to JSON.
- The package imports the engine only through the top-level `nl2sql` namespace
  (`from nl2sql import NL2SQL, QueryResult, UserContext, configure_logging, ...`),
  never an engine submodule. `packages/api/tests/test_architecture.py` enforces it.
- Routers are included under `/api/v1`.
- CORS is restricted to the origins in `api.cors_origins`
  (see [CORS origins](#cors-origins)); with none configured, no cross-origin
  browser access is allowed.

### Dependency injection

Source: `packages/api/src/nl2sql_api/dependencies.py`

- `get_engine(request)` returns the singleton `NL2SQL` instance.
- Service providers (`DatasourceService`, `QueryService`, `LLMService`,
  `IndexingService`, `HealthService`) are created per-request with the engine.
- `get_user_context(request)` (in `nl2sql_api/auth.py`) returns the caller's
  `UserContext`; see [Caller role](#caller-role).
- `require_admin(request)` (same module) guards the routes that change
  datasources, LLMs or the index; see [Admin routes](#admin-routes).

## Interactive docs

FastAPI serves Swagger UI at `/docs`, ReDoc at `/redoc` and the OpenAPI schema at
`/openapi.json`, with a summary, description and tag on every route. Start the
server on a config:

```bash
NL2SQL_CONFIG=nl2sql-demo/nl2sql.yaml nl2sql-api --host 127.0.0.1 --port 8000   # then open http://localhost:8000/docs
```

## API Index

| API | Router Path | Purpose |
| --- | --- | --- |
| [Health](health.md) | `routes/health.py` | Liveness/readiness checks. |
| [Query](query.md) | `routes/query.py` | Execute natural language queries, whole or streamed as Server-Sent Events; page through or download a run's saved result. |
| [Datasource](datasource.md) | `routes/datasource.py` | Manage datasource configs. |
| [LLM](llm.md) | `routes/llm.py` | Configure and inspect LLMs. |
| [Indexing](indexing.md) | `routes/indexing.py` | Index management and status. |

## Error Handling

FastAPI routers wrap most failures in `HTTPException(status_code=500, detail=str(e))`.
Datasource and delete endpoints map `ValueError` to `HTTP 404`.

`POST /api/v1/query` is the exception: pipeline errors are returned as a normal
`HTTP 200` inside `QueryResponse.errors`, and an unexpected failure returns a
generic `HTTP 500` with the traceback logged server-side rather than returned.
`POST /api/v1/datasource` does the same: its exception can carry a connection
string, password included, so the response says only that it failed and the
cause goes to the server log.

Route handlers that call blocking engine code are declared with `def` rather than
`async def`, so Starlette runs them in its threadpool instead of blocking the
event loop.

## Configuration

The REST API is configured by the same `nl2sql.yaml` as the engine. Beyond the
runtime server options in `nl2sql_api.server` (`--host`, `--port`, `--reload`),
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

### Caller role

The engine enforces RBAC; the API only decides which role a request carries. It
does not authenticate callers, and it never takes the role from the request body
unless a dev flag says so. The `get_user_context` dependency reads, first match
wins:

| `api` key | Role source |
| --- | --- |
| `role_header` | The name of a header a trusted proxy sets, e.g. `X-NL2SQL-Role`. Comma-separated values are several roles. Configure it only when the proxy authenticates the caller and overwrites the header on every request; otherwise any client can pick its own role. |
| `role` | One static role for every request, e.g. `admin` for a single-tenant deployment or the demo. |
| `trust_body_role` | `true` takes `user_context` from the request body. For local testing only: any client can choose any role. The server logs a warning at startup when it is on. |

With no role from any of them, `POST /api/v1/query` and the result routes answer `HTTP 401` with a
message naming these keys; the engine never runs.

```yaml
# api.yaml for the demo: every request runs as admin.
role: admin
```

```yaml
# api.yaml behind an authenticating proxy that sets X-NL2SQL-Role.
role_header: X-NL2SQL-Role
```

This is a seam, not an auth provider: to integrate one, override the
`get_user_context` dependency (`app.dependency_overrides[get_user_context] = ...`)
or put the provider in the proxy.

### Admin routes

The routes that change the running process need an admin role, checked by the
`require_admin` dependency in `nl2sql_api/auth.py`:

- `POST /api/v1/datasource` and `DELETE /api/v1/datasource/{datasource_id}`
- `POST /api/v1/llm`
- `POST /api/v1/index/{datasource_id}`, `POST /api/v1/index-all` and `DELETE /api/v1/index`

| Key | Default | Meaning |
| --- | --- | --- |
| `policies.admin_roles` | `[admin]` | The roles that may call the admin routes. |

The roles come from the same sources as [Caller role](#caller-role). A request
whose roles include one in `policies.admin_roles` is let through; one with
roles but none of them admin is `HTTP 403`; one with no role is `HTTP 401`.

With **none** of `api.role_header`, `api.role` or `api.trust_body_role` set,
admin routes answer only clients on loopback (`127.0.0.1`, `::1`) and return
`HTTP 403` to everyone else, with a message naming the keys to set. A fresh local install can still register and
index datasources, and a port published by mistake does not hand them out.
Two consequences:

- In Docker, a request through a published port arrives from the bridge
  gateway, not loopback, so admin routes need a role setting there.
- A reverse proxy on the same host makes every request look like loopback.
  Behind one, configure `api.role_header`.

The read-only routes (`/health`, `/ready`, `GET /datasource`, `GET /llm`, which
never returns API keys, and `GET /index/status`) need no role.

```yaml
# The proxy sets X-NL2SQL-Role; only "ops" may change datasources and the index.
# api.yaml
role_header: X-NL2SQL-Role
# policies.yaml (with the roles)
admin_roles: [ops]
```
