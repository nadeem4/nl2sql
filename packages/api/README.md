# NL2SQL API

FastAPI REST service for the NL2SQL engine (`nl2sql-engine`). It builds one
`NL2SQL` engine at startup, from the same env file and configs as the CLI, and
serves it under `/api/v1`.

## Running the API

```bash
pip install nl2sql-api
ENV=demo NL2SQL_API_ROLE=admin nl2sql-api --host 127.0.0.1 --port 8000 [--reload]
# or: python -m nl2sql_api.server ..., or: uvicorn nl2sql_api.main:app
```

Start it from the folder holding the env file and configs (the demo's use
relative paths). Interactive docs: Swagger UI at `/docs`, ReDoc at `/redoc`,
the schema at `/openapi.json`.

The API does not authenticate callers and never takes the RBAC role from the
request body. Set one of: `NL2SQL_API_ROLE_HEADER` (a header a trusted proxy
sets), `NL2SQL_API_ROLE` (one static role) or, for local testing only,
`NL2SQL_API_TRUST_BODY_ROLE=true` (the body's `user_context`; logs a warning).
With none, `/api/v1/query` answers HTTP 401.

## Endpoints

- `POST /api/v1/query` - Ask a question as the configured role
- `GET /api/v1/health` - Liveness check
- `GET /api/v1/ready` - Readiness check (does not yet check dependencies)
- `POST /api/v1/datasource` - Register a datasource in the running process
- `GET /api/v1/datasource` - List registered datasource ids
- `GET /api/v1/datasource/{datasource_id}` - Check a datasource is registered
- `DELETE /api/v1/datasource/{datasource_id}` - Not supported yet (answers `success: false`)
- `POST /api/v1/llm` - Configure an LLM in the running process
- `GET /api/v1/llm` - List configured LLMs
- `GET /api/v1/llm/{llm_name}` - Get one configured LLM
- `POST /api/v1/index/{datasource_id}` - Index one datasource's schema
- `POST /api/v1/index-all` - Index every registered datasource
- `DELETE /api/v1/index` - Clear the vector store
- `GET /api/v1/index/status` - Placeholder status (the registered datasource ids)

Reference: <https://github.com/nadeem4/nl2sql/blob/main/docs/api/rest/index.md>.
