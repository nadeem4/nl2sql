# Kubernetes Deployment

Kubernetes deployment depends on the service you build around the engine, or on
`nl2sql-api`. The engine expects one config, `nl2sql.yaml`, and the variables it
names to be available to the runtime container.

## Configuration and secrets

Mount the config folder (a ConfigMap of `nl2sql.yaml` and the files it
includes) and inject secrets so that:

- `NL2SQL_CONFIG` names the mounted `nl2sql.yaml` (the API image defaults to
  `/etc/nl2sql/nl2sql.yaml`);
- provider keys (e.g., `OPENAI_API_KEY`) and every variable the config reads as
  `${env:VAR}` (database passwords, `NL2SQL_DATA_DIR`) are injected into env,
  or a secret provider in the `secrets` section reaches them.

Keys never go in the ConfigMap: the config holds references
(`password: "${env:SALES_DB_PASSWORD}"`), not values.

## Persistence

Everything the engine writes lives under `storage.path`: the vector index
(`index/`), the state store (`state.db`), run traces (`traces/`) and result
files (`results/`). Mount one persistent volume and point `storage.path` at it
(`path: "${env:NL2SQL_DATA_DIR}"`). See [Storage](../configuration/storage.md).

## Operational considerations

- Run schema indexing as a separate job or init container:
  `nl2sql --config /etc/nl2sql/nl2sql.yaml index`, on the same volume.
- The API calls `engine.check()` before it serves. A datasource with
  `required: true` that cannot connect stops the pod, so a readiness gate on
  the process is enough; any other datasource that fails is logged and left
  out.
- If you run `nl2sql-api`, set the role source in the `api` section
  (`api.role_header` behind an authenticating ingress, or `api.role`) and the
  admin roles (`policies.admin_roles`, default `[admin]`): pod traffic is not
  loopback, so without them the admin routes answer HTTP 403. See
  [Admin routes](../api/rest/index.md#admin-routes).
- Configure observability exporters in `settings.observability` (`exporter`,
  `otlp_endpoint`).

## Reference configuration

See [Configuration](../configuration/system.md) for the whole config and the
environment variables it reads.
