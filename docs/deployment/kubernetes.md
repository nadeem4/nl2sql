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
- If you run `nl2sql-api`, set `api.auth` (`proxy` behind an authenticating
  ingress with `api.proxy.trusted_from` the ingress's CIDR, or `api_keys`) and
  the admin roles (`policies.admin_roles`, default `[admin]`): pod traffic is not
  the local operator, so under the default `none` every route answers HTTP 403.
  See [Admin routes](../api/rest/index.md#admin-routes).
- Configure observability exporters as observers in the `observers` section
  (an `otel` observer with its `endpoint`, with `nl2sql-engine[otel]`
  installed; see [Observers](../configuration/observers.md#otel)), and set
  `NL2SQL_LOG_FORMAT=json` for JSON log lines. The audit log is under
  `storage.path/audit/`, on the same volume as the engine's other data.

## Reference configuration

See [Configuration](../configuration/system.md) for the whole config and the
environment variables it reads.
