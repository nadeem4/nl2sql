# Kubernetes Deployment

Kubernetes deployment depends on the service you build around `run_with_graph()`. The core engine expects configuration files and environment variables to be available to the runtime container.

## Configuration and secrets

Mount your `configs/` directory and secrets so that:

- `LLM_CONFIG`, `DATASOURCE_CONFIG`, `POLICIES_CONFIG`, and `SECRETS_CONFIG` resolve correctly
- `OPENAI_API_KEY` (and other provider keys) are injected as environment variables

## Persistence

If you persist schema or vector stores, mount volumes for:

- `VECTOR_STORE` (Chroma persistence directory)
- `SCHEMA_STORE_PATH` (SQLite schema store)
- `RESULT_ARTIFACT_BASE_URI` (artifact storage base path)

## Reference configuration

See `configuration/system.md` for settings and defaults.
