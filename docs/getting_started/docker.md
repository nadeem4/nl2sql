# Docker (REST API)

Use this path when you want the HTTP API only.

## Build the image

From the repo root:

```bash
# Dev image (local source)
docker build -f packages/api/Dockerfile.dev -t nl2sql-api-dev .

# PyPI image (stable releases)
docker build -f packages/api/Dockerfile -t nl2sql-api .
```

## Install adapters

You choose adapters at build time:

```bash
docker build -f packages/api/Dockerfile --build-arg NL2SQL_EXTRAS=postgres -t nl2sql-api .
```

For dev images:

```bash
docker build -f packages/api/Dockerfile.dev --build-arg NL2SQL_EXTRAS=all -t nl2sql-api-dev .
```

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
