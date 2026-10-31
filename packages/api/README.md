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
nl2sql-api serve --config nl2sql-demo/nl2sql.yaml --port 8000     # 127.0.0.1, one worker
# or: python -m nl2sql_api serve ..., or: NL2SQL_CONFIG=... uvicorn nl2sql_api.main:app
```

Interactive docs: Swagger UI at `/docs`, ReDoc at `/redoc`, the schema at
`/openapi.json`.

The API's own settings are the `api` section of `nl2sql.yaml`; the engine
ignores it:

```yaml
api:
  auth: static                   # api_keys | proxy | static | none (default none)
  static_role: admin             # auth: static only
  cors_origins: []               # browser origins allowed to call the API
```

`api.auth` picks who is asking; the request body never carries the caller. With no
identity the routes answer HTTP 401. Under the default `none` only a request made
straight to `localhost` is answered, and `serve` refuses to bind another host.
Every error is one envelope, `{"error": {"code", "message", "fix", "details", "request_id"}}`,
with the request id also in the `X-Request-ID` header.

The routes that change datasources, LLMs or the index need a role listed in
`policies.admin_roles` (default `[admin]`), else HTTP 403.

The Docker image (`packages/api/Dockerfile`) reads `/etc/nl2sql/nl2sql.yaml`:
mount a config folder there, and a volume for `storage.path`.

## Endpoints

- `POST /api/v1/query` - Ask a question as the configured role
- `POST /api/v1/query/stream` - The same, as Server-Sent Events: each step as it runs, the result manifest, the answer as it is written, then the `/query` response
- `GET /api/v1/health` - Liveness check
- `GET /api/v1/ready` - Readiness check (does not yet check dependencies)
- `GET /api/v1/meta` - The mode, the identity source and `request_llm` (no caller needed)
- `GET /api/v1/about` - What the engine is
- `GET /api/v1/datasource-types` - The adapters the engine can name, each with its connection schema (admin only)
- `POST /api/v1/datasource/test` - Test a connection without registering it; returns the failed stage and the fix (admin only)
- `POST /api/v1/datasource` - Register a datasource in the running process (`config` is one `datasources` entry: `id`, `type`, `connection`, ...); `save: true` also writes `datasources/<id>.yaml` with secrets as references; a body that does not validate is a 422 listing each problem by path
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
