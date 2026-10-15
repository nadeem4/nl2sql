# Docker Deployment

NL2SQL can be deployed as a standalone HTTP service using the `nl2sql-api` package and Docker. You can also embed the core library in your own service.

For the playground served as a public demo of the sample databases, where each
visitor brings their own API key, see [Hosted demo](hosted-demo.md).

## Container requirements

- Install `nl2sql-engine` and add adapters via extras (e.g. `nl2sql-engine[postgres]`).
- Provide the config: `nl2sql.yaml` and the files it includes, at the path
  `NL2SQL_CONFIG` names. `packages/api/Dockerfile` sets
  `NL2SQL_CONFIG=/etc/nl2sql/nl2sql.yaml`, so mount a config folder there.
- Provide the provider keys and any variable the config reads as `${env:VAR}`,
  in the environment or in a `.env` next to `nl2sql.yaml` (loaded at startup,
  never overriding a variable already set).

## Persistence mounts

Everything the engine writes lives under `storage.path`: the vector index
(`index/`), the state store (`state.db`: schema snapshots, the plan cache and
feedback), run traces (`traces/`) and result files (`results/`). Mount one
volume and point `storage.path` at it, typically from the environment:

```yaml
# storage.yaml
path: "${env:NL2SQL_DATA_DIR}"
```

```bash
docker run --rm -p 8000:8000 \
  -v "$PWD/config:/etc/nl2sql:ro" \
  -v nl2sql-data:/data -e NL2SQL_DATA_DIR=/data \
  -e OPENAI_API_KEY \
  nl2sql-api
```

See [Storage](../configuration/storage.md).

## Startup

The API builds the engine from the config and calls `engine.check()` before it
accepts a request. A datasource with `required: true` that cannot connect stops
the container (`EngineNotReady` in the log); any other one that fails is logged
and left out of routing. Mark the datasources the service cannot run without
`required: true`, so the orchestrator restarts a container that started broken.

## API roles

`nl2sql-api` does not authenticate callers. Its `api` section says where the
RBAC role comes from (`api.role_header`, a header an authenticating proxy sets,
or a static `api.role`), and `policies.admin_roles` (default `[admin]`) says
which roles may change datasources, LLMs and the index. With no role source,
queries answer HTTP 401 and the admin routes answer HTTP 403, because a request
through a published port does not come from loopback. See
[API settings](../configuration/api.md),
[Caller role](../api/rest/index.md#caller-role) and
[Admin routes](../api/rest/index.md#admin-routes).

## Notes

- Schema indexing is not automatic; you must run indexing in your operational
  flow: `nl2sql --config /etc/nl2sql/nl2sql.yaml index` against the same
  storage, or `POST /api/v1/index-all` (admin role).
- Observability exporters require network access. From release 0.7 they
  are observers in the config's `observers` section (an `otel` observer with
  its `endpoint`); `NL2SQL_LOG_FORMAT=json` gives JSON log lines.

## Reference configuration

See [Configuration](../configuration/system.md) for the whole config and the
environment variables it reads.
