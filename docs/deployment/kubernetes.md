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
- If you run `nl2sql-api`, set the role source (`NL2SQL_API_ROLE_HEADER` behind an
  authenticating ingress, or `NL2SQL_API_ROLE`) and the admin roles
  (`NL2SQL_API_ADMIN_ROLES`, default `admin`): pod traffic is not loopback, so
  without them the admin routes answer HTTP 403. See
  [Admin routes](../api/rest/index.md#admin-routes).
- Configure observability exporters via env (`OBSERVABILITY_EXPORTER`, `OTEL_EXPORTER_OTLP_ENDPOINT`).

## Reference configuration

See `configuration/system.md` for settings and defaults.
