# REST API (FastAPI)

This section documents the `nl2sql-api` FastAPI package that exposes the core
engine over HTTP. Implementation lives in `packages/api/src/nl2sql_api/`.

## Runtime Model

### Application lifecycle

Source: `packages/api/src/nl2sql_api/main.py`

- `lifespan` configures logging, initializes a single `NL2SQL` engine and stores it
  in `app.state.engine`. When `observability_exporter` is `otlp` (read through
  `engine.get_setting`), logs switch to JSON.
- The package imports the engine only through the top-level `nl2sql` namespace
  (`from nl2sql import NL2SQL, QueryResult, UserContext, configure_logging, ...`),
  never an engine submodule. `packages/api/tests/test_architecture.py` enforces it.
- Routers are included under `/api/v1`.
- CORS is restricted to the origins listed in `NL2SQL_API_CORS_ORIGINS`
  (see [CORS origins](#cors-origins)); with none configured, no cross-origin
  browser access is allowed.

### Dependency injection

Source: `packages/api/src/nl2sql_api/dependencies.py`

- `get_engine(request)` returns the singleton `NL2SQL` instance.
- Service providers (`DatasourceService`, `QueryService`, `LLMService`,
  `IndexingService`, `HealthService`) are created per-request with the engine.
- `get_user_context(request)` (in `nl2sql_api/auth.py`) returns the caller's
  `UserContext`; see [Caller role](#caller-role).

## Interactive docs

FastAPI serves Swagger UI at `/docs`, ReDoc at `/redoc` and the OpenAPI schema at
`/openapi.json`, with a summary, description and tag on every route. Start the
server from the folder holding your env file and configs:

```bash
ENV=demo nl2sql-api --host 127.0.0.1 --port 8000   # then open http://localhost:8000/docs
```

## API Index

| API | Router Path | Purpose |
| --- | --- | --- |
| [Health](health.md) | `routes/health.py` | Liveness/readiness checks. |
| [Query](query.md) | `routes/query.py` | Execute natural language queries. |
| [Datasource](datasource.md) | `routes/datasource.py` | Manage datasource configs. |
| [LLM](llm.md) | `routes/llm.py` | Configure and inspect LLMs. |
| [Indexing](indexing.md) | `routes/indexing.py` | Index management and status. |

## Error Handling

FastAPI routers wrap most failures in `HTTPException(status_code=500, detail=str(e))`.
Datasource and delete endpoints map `ValueError` to `HTTP 404`.

`POST /api/v1/query` is the exception: pipeline errors are returned as a normal
`HTTP 200` inside `QueryResponse.errors`, and an unexpected failure returns a
generic `HTTP 500` with the traceback logged server-side rather than returned.

Route handlers that call blocking engine code are declared with `def` rather than
`async def`, so Starlette runs them in its threadpool instead of blocking the
event loop.

## Configuration

The REST API inherits all configuration from the Core engine. Beyond the runtime
server options in `nl2sql_api.server` (`--host`, `--port`, `--reload`), it reads
these environment variables of its own.

### CORS origins

`NL2SQL_API_CORS_ORIGINS` is a comma-separated list of the browser origins
allowed to call the API cross-origin. Each entry is a full origin (scheme, host
and, if non-default, port); surrounding whitespace is ignored.

```bash
export NL2SQL_API_CORS_ORIGINS="https://bi.corp.example,http://localhost:3000"
```

With one or more origins configured, only those origins are accepted and
credentialed requests (cookies, `Authorization`) are permitted.

If the variable is unset or empty, no origins are allowed and credentials are
disabled. That is the intended default, not a misconfiguration: a browser page
served from another origin cannot call the API until you opt it in. The value is
read once at import time, so changing it requires restarting the server.

This setting governs the browser same-origin policy only. Non-browser clients --
`curl`, server-side callers, the Python SDK -- do not send an `Origin` header and
are unaffected by it; use authentication and network controls to restrict those.

### Caller role

The engine enforces RBAC; the API only decides which role a request carries. It
does not authenticate callers, and it never takes the role from the request body
unless a dev flag says so. The `get_user_context` dependency reads, first match
wins:

| Variable | Role source |
| --- | --- |
| `NL2SQL_API_ROLE_HEADER` | The name of a header a trusted proxy sets, e.g. `X-NL2SQL-Role`. Comma-separated values are several roles. Configure it only when the proxy authenticates the caller and overwrites the header on every request; otherwise any client can pick its own role. |
| `NL2SQL_API_ROLE` | One static role for every request, e.g. `admin` for a single-tenant deployment or the demo. |
| `NL2SQL_API_TRUST_BODY_ROLE` | `true` takes `user_context` from the request body, as the API did before. For local testing only: any client can choose any role. The server logs a warning at startup when it is on. |

With no role from any of them, `POST /api/v1/query` answers `HTTP 401` with a
message naming these variables; the engine never runs. The variables are read
on each request.

```bash
# Demo: every request runs as admin.
ENV=demo NL2SQL_API_ROLE=admin nl2sql-api --port 8000

# Behind an authenticating proxy that sets X-NL2SQL-Role.
NL2SQL_API_ROLE_HEADER=X-NL2SQL-Role nl2sql-api --port 8000
```

This is a seam, not an auth provider: to integrate one, override the
`get_user_context` dependency (`app.dependency_overrides[get_user_context] = ...`)
or put the provider in the proxy.
