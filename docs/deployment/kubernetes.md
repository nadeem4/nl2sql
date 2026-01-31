# Kubernetes Deployment

Kubernetes deployment depends on the service you build around `run_with_graph()`. The core engine expects configuration files and environment variables to be available to the runtime container.

## Configuration and secrets

Mount `configs/` and inject secrets so that:

- `LLM_CONFIG`, `DATASOURCE_CONFIG`, `POLICIES_CONFIG`, `SECRETS_CONFIG` resolve correctly.
- provider keys (e.g., `OPENAI_API_KEY`) are injected into env.

## Persistence

If you persist schema/vector stores or artifacts, mount volumes for:

- `VECTOR_STORE` (Chroma persistence directory)
- `SCHEMA_STORE_PATH` (SQLite schema store)
- `RESULT_ARTIFACT_BASE_URI` (artifact storage base path)

## Operational considerations

- Run schema indexing as a separate job or init container.
- Configure observability exporters via env (`OBSERVABILITY_EXPORTER`, `OTEL_EXPORTER_OTLP_ENDPOINT`).

## Reference configuration

See `configuration/system.md` for settings and defaults.
