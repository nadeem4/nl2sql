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

## Run the API

```bash
docker run --rm -p 8000:8000 nl2sql-api
```

To set environment selection:

```bash
docker run --rm -p 8000:8000 -e ENV=demo -e NL2SQL_API_ROLE=admin nl2sql-api
```

`NL2SQL_API_ROLE` pins the RBAC role for every request; without a role setting
the query route answers HTTP 401. See [Caller role](../api/rest/index.md#caller-role).

The routes that register datasources or LLMs and build or clear the index need a
role listed in `NL2SQL_API_ADMIN_ROLES` (default `admin`), so `NL2SQL_API_ROLE=admin`
above allows them. Without any role setting they answer loopback clients only,
and a request through a published Docker port does not come from loopback: it
gets HTTP 403. See [Admin routes](../api/rest/index.md#admin-routes).

## Configuration

Mount or bake your config files into the container:

- `configs/datasources.yaml`
- `configs/llm.yaml`
- `configs/policies.json`
- `configs/secrets.yaml` (optional)

See `configuration/system.md` for environment variables and defaults.

## API usage

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"natural_language":"Top 5 customers by revenue last quarter?"}'
```
