# From Source (Development)

Use this path when you want to contribute or run the latest changes.

## Clone and install

```bash
git clone https://github.com/nadeem4/nl2sql.git
cd nl2sql

python -m venv venv
source venv/bin/activate

pip install -e packages/adapter-sdk
pip install -e packages/adapter-sdk
pip install -e "packages/nl2sql[postgres]"
pip install -e packages/api
```

Add any other adapters you need (mysql, mssql, duckdb, sqlite).

## Configuration

Create config files in your working directory:

- `configs/datasources.yaml`
- `configs/llm.yaml`
- `configs/policies.json`
- `configs/secrets.yaml` (optional)

Start from `configs/*.example.yaml` and `configs/*.example.json`.

## Run locally

Python API:

```bash
python -c "from nl2sql import NL2SQL; print(NL2SQL().run_query('hello'))"
```

API service (Docker):

```bash
docker build -f packages/api/Dockerfile.dev --build-arg NL2SQL_EXTRAS=postgres -t nl2sql-api-dev .
docker run --rm -p 8000:8000 nl2sql-api-dev
```

See [Docker (REST API)](docker.md) for adapter extras, environment selection
and mounting config files.
