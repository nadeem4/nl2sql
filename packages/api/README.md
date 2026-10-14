# NL2SQL API

FastAPI REST service for the NL2SQL engine (`nl2sql-engine`). At startup it
loads the `.env` next to `nl2sql.yaml` (never overriding a variable already
set), builds one engine with `NL2SQL.from_config()` from the same config as the
CLI (`NL2SQL_CONFIG`, else `./nl2sql.yaml`), calls `engine.check()`, and serves
it under `/api/v1`. A datasource marked `required: true` that cannot connect
stops it from starting; any other one that fails is left out.

## Running the API

```bash
pip install nl2sql-api
NL2SQL_CONFIG=nl2sql-demo/nl2sql.yaml nl2sql-api --host 127.0.0.1 --port 8000 [--reload]
# or: python -m nl2sql_api.server ..., or: uvicorn nl2sql_api.main:app
```

Interactive docs: Swagger UI at `/docs`, ReDoc at `/redoc`, the schema at
`/openapi.json`.

The API's own settings are the `api` section of `nl2sql.yaml`; the engine
ignores it:

```yaml
api:
  role_header: X-NL2SQL-Role     # a header a trusted proxy sets on every request
  role: null                     # or one static role for every request
  trust_body_role: false         # local testing only: the body's user_context
  cors_origins: []               # browser origins allowed to call the API
```

The API does not authenticate callers and never takes the RBAC role from the
request body unless `api.trust_body_role` is on (it logs a warning). With no
role source, `/api/v1/query` and `/api/v1/query/stream` answer HTTP 401.

The routes that change datasources, LLMs or the index need a role listed in
`policies.admin_roles` (default `[admin]`), else HTTP 403. With no role setting
at all they answer loopback clients only.

The Docker image (`packages/api/Dockerfile`) reads `/etc/nl2sql/nl2sql.yaml`:
mount a config folder there, and a volume for `storage.path`.

## Endpoints

- `POST /api/v1/query` - Ask a question as the configured role
- `POST /api/v1/query/stream` - The same, as Server-Sent Events: each step as it runs, the result manifest, the answer as it is written, then the `/query` response
- `GET /api/v1/health` - Liveness check
- `GET /api/v1/ready` - Readiness check (does not yet check dependencies)
- `POST /api/v1/datasource` - Register a datasource in the running process (`config` is one `datasources` entry: `id`, `type`, `connection`, ...)
- `GET /api/v1/datasource` - List the datasources, with their status and masked connections
- `GET /api/v1/datasource/{datasource_id}` - One datasource
- `DELETE /api/v1/datasource/{datasource_id}` - Take a datasource out of the running process
- `PUT /api/v1/settings/llm` - Replace the whole `llm` section in the running process (admin only; 422 with the config error)
- `GET /api/v1/llm` - Each agent and the model it runs on
- `GET /api/v1/llm/{agent}` - One agent's model
- `POST /api/v1/index/{datasource_id}` - Index one datasource's schema
- `POST /api/v1/index-all` - Index every configured datasource
- `DELETE /api/v1/index` - Clear the vector store
- `GET /api/v1/index/status` - Index health (status, entries by type, embedding model, problems)

Reference: <https://github.com/nadeem4/nl2sql/blob/main/docs/api/rest/index.md>.
