# Docker Deployment

NL2SQL can be deployed as a standalone HTTP service using the `nl2sql-api` package and Docker. You can also embed the core library in your own service.

For the playground served as a public demo of the sample databases, where each
visitor brings their own API key, see [Hosted demo](hosted-demo.md).

## Container requirements

- Install `nl2sql-engine` and add adapters via extras (e.g. `nl2sql-engine[postgres]`).
- Provide configuration files under `configs/` (or configure paths via env vars).
- Set required environment variables (LLM keys, config paths, storage paths).

## Persistence mounts

If using persistent stores, mount these paths:

- `VECTOR_STORE` for the Chroma vector store.
- `SCHEMA_STORE_PATH` for the SQLite schema store.
- `RESULT_ARTIFACT_BASE_URI` for artifact storage (local backend).

## API roles

`nl2sql-api` does not authenticate callers. Set where the RBAC role comes from
(`NL2SQL_API_ROLE_HEADER` from an authenticating proxy, or a static
`NL2SQL_API_ROLE`) and which roles may change datasources, LLMs and the index
(`NL2SQL_API_ADMIN_ROLES`, default `admin`). With no role setting, queries answer
HTTP 401 and the admin routes answer HTTP 403, because a request through a
published port does not come from loopback. See
[Caller role](../api/rest/index.md#caller-role) and
[Admin routes](../api/rest/index.md#admin-routes).

## Notes

- Schema indexing is not automatic; you must run indexing in your operational flow.
  Through the API, `POST /api/v1/index-all` needs an admin role.
- Observability exporters require network access and corresponding env vars.

## Reference configuration

See `configuration/system.md` for settings and defaults.
