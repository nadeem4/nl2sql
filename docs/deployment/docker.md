# Docker Deployment

The core engine is a Python library. This repository does **not** ship an HTTP server or a production image; deployments typically embed `run_with_graph()` in a service you own.

## Container requirements

- Install `packages/core` (and adapter packages you use).
- Provide configuration files under `configs/`.
- Set required environment variables (e.g., `OPENAI_API_KEY`, `LLM_CONFIG`, `DATASOURCE_CONFIG`).

## Persisted state

If you use a persistent vector store (`VECTOR_STORE`) or SQLite schema store (`SCHEMA_STORE_PATH`), mount the corresponding directories into the container.

## Reference configuration

See `configuration/system.md` for the full list of settings and default paths.
