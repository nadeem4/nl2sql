# From Source (Development)

Use this path when you want to contribute or run the latest changes.

## Clone and install

```bash
git clone https://github.com/nadeem4/nl2sql.git
cd nl2sql

python -m venv venv
source venv/bin/activate

pip install -e packages/adapter-sdk
pip install -e packages/core
pip install -e packages/adapter-sqlalchemy
pip install -e packages/adapters/postgres
```

Add any other adapters you need (mysql, mssql, sqlite).

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
python run.py --dockerfile packages/api/Dockerfile.dev --extras postgres
```
