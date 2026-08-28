# Docker (REST API)

Use this path when you want the HTTP API only.

## Build the image

From the repo root:

```bash
docker build -f packages/api/Dockerfile -t nl2sql-api .
```

The image installs the local packages from the build context -
`packages/adapter-sdk`, `packages/nl2sql[postgres,mysql]` and `packages/api` - so
the build context has to be the repo root.

## Adapters

The image ships the Postgres and MySQL drivers. Every dialect adapter is part of
the `nl2sql-engine` distribution; an extra only adds the driver it needs, so add another
extra to the `pip install` line in `packages/api/Dockerfile` to include one -
for example `"./packages/nl2sql[postgres,mysql,mssql]"`.

The demo stack builds this same image as its `app` service - see
[Demo data](demo.md#the-docker-demo-stack).

## Run the API

```bash
docker run --rm -p 8000:8000 nl2sql-api
```

To set environment selection:

```bash
docker run --rm -p 8000:8000 -e ENV=demo nl2sql-api
```

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
