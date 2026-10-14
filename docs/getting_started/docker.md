# Docker (REST API)

Use this path when you want the HTTP API only.

## Build the image

From the repo root:

```bash
docker build -f packages/api/Dockerfile -t nl2sql-api .
```

The image installs the local packages from the build context -
`packages/sdk`, `packages/nl2sql[postgres,mysql]` and `packages/api` - so
the build context has to be the repo root. The `COPY ./packages/...` lines
resolve against the context, so `-f packages/api/Dockerfile .` is the only
correct invocation; building with `packages/api` as the context fails with
`"/packages/sdk": not found`.

The root `.dockerignore` keeps that context to the sources the build actually
needs - it drops `.git`, virtualenvs, `docs/`, `site/`, `chroma_db/`, `data/`,
`logs/` and stale `build/`, `dist/` and `*.egg-info` directories.

## Adapters

The image ships the Postgres and MySQL drivers. Every dialect adapter is part of
the `nl2sql-engine` distribution; an extra only adds the driver it needs, so add another
extra to the `pip install` line in `packages/api/Dockerfile` to include one -
for example `"./packages/nl2sql[postgres,mysql,mssql]"`.

## Configuration

The image sets `NL2SQL_CONFIG=/etc/nl2sql/nl2sql.yaml`. Mount a config folder
there: `nl2sql.yaml`, the files it includes, and optionally a `.env` next to
it, which the API loads at startup without overriding variables already set.
Write one with `nl2sql init` (see
[Start a config](index.md#start-a-config-with-nl2sql-init)).

Keep the index, state, traces and results on a volume: write
`storage.path` from an environment variable, and mount the volume there.

```yaml
# storage.yaml
path: "${env:NL2SQL_DATA_DIR}"
```

```yaml
# api.yaml (nl2sql.yaml has  api: { file: api.yaml })
role: admin             # one static role for every request
```

`api.role` pins the RBAC role for every request; without a role source the
query route answers HTTP 401. See [Caller role](../api/rest/index.md#caller-role)
and [API settings](../configuration/api.md).

The routes that register datasources or LLMs and build or clear the index need a
role listed in `policies.admin_roles` (default `[admin]`), so `role: admin`
above allows them. Without any role setting they answer loopback clients only,
and a request through a published Docker port does not come from loopback: it
gets HTTP 403. See [Admin routes](../api/rest/index.md#admin-routes).

## Run the API

```bash
docker run --rm -p 8000:8000 \
  -v "$PWD/my-project:/etc/nl2sql:ro" \
  -v nl2sql-data:/data -e NL2SQL_DATA_DIR=/data \
  nl2sql-api
```

Provider keys come from the environment (`-e OPENAI_API_KEY=...`) or from the
mounted `.env`. Before it serves, the API calls `engine.check()`: a datasource
with `required: true` that cannot connect stops the container, and any other
that fails is logged and left out. Build the index once with
`POST /api/v1/index-all`, or with `nl2sql --config my-project/nl2sql.yaml index`
against the same storage.

## API usage

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"natural_language":"Top 5 customers by revenue last quarter?"}'
```
