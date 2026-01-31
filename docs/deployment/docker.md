# Docker Deployment

NL2SQL ships as a Python library, not a standalone HTTP service. Container images should embed `run_with_graph()` inside your service.

## Container requirements

- Install `packages/core` and required adapter packages.
- Provide configuration files under `configs/`.
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
