# REST API (FastAPI)

This section documents the `nl2sql-api` FastAPI package that exposes the core
engine over HTTP. Implementation lives in `packages/api/src/nl2sql_api/`.

## Runtime Model

### Application lifecycle

Source: `packages/api/src/nl2sql_api/main.py`

- `lifespan` initializes a single `NL2SQL` engine and stores it in `app.state.engine`.
- Routers are included under `/api/v1`.
- CORS is enabled for all origins (intended to be tightened in production).

### Dependency injection

Source: `packages/api/src/nl2sql_api/dependencies.py`

- `get_engine(request)` returns the singleton `NL2SQL` instance.
- Service providers (`DatasourceService`, `QueryService`, `LLMService`,
  `IndexingService`, `HealthService`) are created per-request with the engine.

## API Index

| API | Router Path | Purpose |
| --- | --- | --- |
| [Health](health.md) | `routes/health.py` | Liveness/readiness checks. |
| [Query](query.md) | `routes/query.py` | Execute and fetch query results. |
| [Datasource](datasource.md) | `routes/datasource.py` | Manage datasource configs. |
| [LLM](llm.md) | `routes/llm.py` | Configure and inspect LLMs. |
| [Indexing](indexing.md) | `routes/indexing.py` | Index management and status. |

## Error Handling

FastAPI routers wrap most failures in `HTTPException(status_code=500, detail=str(e))`.
Datasource and delete endpoints map `ValueError` to `HTTP 404`.

## Configuration

The REST API inherits all configuration from the Core engine. It does not add
additional environment variables beyond the runtime server options in
`nl2sql_api.server` (`--host`, `--port`, `--reload`).
