# From Source (Development)

Use this path when you want to contribute or run the latest changes.

## Clone and install

```bash
git clone https://github.com/nadeem4/nl2sql.git
cd nl2sql

python -m venv venv
source venv/bin/activate

pip install -e packages/sdk -e "packages/nl2sql[postgres,duckdb,demo]" -e packages/api
pip install --group dev
```

These are the extras the full test suite needs (see `CONTRIBUTING.md`):
`postgres` and `duckdb` supply the drivers the adapter tests import, and `demo`
supplies the FastAPI/uvicorn server behind `nl2sql demo`. Add `mysql` or
`mssql` for those drivers, or use `[all]` for every dialect driver (it does not
include `demo`). There is no `sqlite` extra: that driver is in the standard
library. `pip install --group dev` (pip 25.1 or newer) installs the PEP 735
`dev` group from the root `pyproject.toml`: `pytest`, `pytest-randomly`,
`pytest-timeout` and `httpx`.

## Configuration

The engine reads one `nl2sql.yaml`. `nl2sql init` writes it, with each section
in its own file, and `nl2sql init --demo` writes the demo's. See
[Start a config with `nl2sql init`](index.md#start-a-config-with-nl2sql-init)
and [Configuration](../configuration/system.md).

```bash
nl2sql init my-demo --demo
nl2sql --config my-demo/nl2sql.yaml index
```

## Run locally

CLI:

```bash
nl2sql --config my-demo/nl2sql.yaml run "How many customers are there?"
```

Python API:

```bash
python -c "from nl2sql import NL2SQL, UserContext; print(NL2SQL.from_config('my-demo/nl2sql.yaml').run_query('How many customers are there?', user_context=UserContext(roles=['admin'])))"
```

API service:

```bash
NL2SQL_CONFIG=my-demo/nl2sql.yaml nl2sql-api --port 8000
```

or in Docker:

```bash
docker build -f packages/api/Dockerfile -t nl2sql-api .
docker run --rm -p 8000:8000 -v "$PWD/my-demo:/etc/nl2sql" nl2sql-api
```

See [Docker (REST API)](docker.md) for adapter extras, the role settings and
the data volume.
