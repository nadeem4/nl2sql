# From Source (Development)

Use this path when you want to contribute or run the latest changes.

## Clone and install

```bash
git clone https://github.com/nadeem4/nl2sql.git
cd nl2sql

python -m venv venv
source venv/bin/activate

pip install -e packages/adapter-sdk -e "packages/nl2sql[postgres,duckdb,demo]" -e packages/api
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

Create config files in your working directory:

- `configs/datasources.yaml`
- `configs/llm.yaml`
- `configs/policies.json`
- `configs/secrets.yaml` (optional)

Start from `configs/datasources.example.yaml` and `configs/policies.example.json`.

## Run locally

Python API:

```bash
python -c "from nl2sql import NL2SQL; print(NL2SQL().run_query('hello'))"
```

API service (Docker):

```bash
docker build -f packages/api/Dockerfile -t nl2sql-api .
docker run --rm -p 8000:8000 nl2sql-api
```

See [Docker (REST API)](docker.md) for adapter extras, environment selection
and mounting config files.
