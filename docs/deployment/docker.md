# Docker Deployment

NL2SQL can be deployed as a standalone HTTP service using the `nl2sql-api` package and Docker. You can also embed the core library in your own service.

## Container requirements

- Install `nl2sql-core` and add adapters via extras (e.g. `nl2sql-core[postgres]`).
- Provide configuration files under `configs/` (or configure paths via env vars).
- Set required environment variables (LLM keys, config paths, storage paths).

## Persistence mounts

If using persistent stores, mount these paths:

- `VECTOR_STORE` for the Chroma vector store.
- `SCHEMA_STORE_PATH` for the SQLite schema store.
- `RESULT_ARTIFACT_BASE_URI` for artifact storage (local backend).

## Notes

- Schema indexing is not automatic; you must run indexing in your operational flow.
- Observability exporters require network access and corresponding env vars.

## Reference configuration

See `configuration/system.md` for settings and defaults.
