# Docker (REST API)

Use this path when you want the HTTP API only.

## Build the image

From the repo root:

```bash
docker build -t nl2sql-api .
```

The `Dockerfile` is at the repo root. Its first stage builds the web app
(`nl2sql-ui`); the second installs the local packages from the build context -
`packages/sdk`, `packages/nl2sql[postgres,mysql,mssql,duckdb,anthropic,otel]` and
`packages/api` - and serves the built app, so the build context has to be the
repo root. For a running stack with a volume and API keys, see
[Docker Compose](../deployment/compose.md).

The root `.dockerignore` keeps that context to the sources the build actually
needs - it drops `.git`, virtualenvs, `docs/`, `site/`, `chroma_db/`, `data/`,
`logs/` and stale `build/`, `dist/` and `*.egg-info` directories.

## Adapters

The image ships all five adapters' drivers (PostgreSQL, MySQL, SQL Server with
Microsoft's ODBC driver 18, DuckDB, SQLite) and the Anthropic provider. One image,
no slim variant.

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
auth: static
static_role: admin      # one role for every request
```

`api.auth: static` pins the RBAC role for every request; the default, `none`,
answers only requests made straight to `localhost`, so a request through a
published Docker port is HTTP 403. See [Identity](../api/rest/index.md#identity)
and [API settings](../configuration/api.md).

The routes that register datasources or LLMs and build or clear the index need a
role listed in `policies.admin_roles` (default `[admin]`), so `static_role: admin`
above allows them. See [Admin routes](../api/rest/index.md#admin-routes).

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
`POST /api/v1/jobs/index`, or with `nl2sql --config my-project/nl2sql.yaml index`
against the same storage.

## API usage

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"question":"Top 5 customers by revenue last quarter?"}'
```
