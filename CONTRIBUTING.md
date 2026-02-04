# Contributing to NL2SQL

Thanks for contributing to the `nl2sql` monorepo. This guide covers local setup,
tests, documentation, and adapter development.

## Monorepo layout

- `packages/core`: Core engine and pipeline.
- `packages/api`: FastAPI REST service.
- `packages/adapter-sdk`: Adapter interfaces and contracts.
- `packages/adapter-sqlalchemy`: SQLAlchemy adapter base.
- `packages/adapters/*`: Database adapter implementations.
- `docs/`: MkDocs documentation.

## Prerequisites

- Python 3.10+
- Docker (required for integration tests that spin up databases)

## Local setup

1. Clone the repository.
2. Create and activate a virtual environment.
3. Install editable packages you plan to work on.

Example (PowerShell):

```powershell
python -m venv venv
.\venv\Scripts\activate
python -m pip install -e packages/adapter-sdk
python -m pip install -e packages/core
python -m pip install -e packages/adapter-sqlalchemy
python -m pip install -e packages/adapters/postgres
```

## Running tests

Unit tests:

```bash
pytest packages/core/tests/unit
```

Integration tests (requires Docker):

```powershell
./scripts/test_integration.ps1
```

## Documentation

Docs are built with MkDocs. To run locally:

```bash
python -m pip install -r requirements-docs.txt
mkdocs serve
```

## Contribution workflow

1. Create a feature branch (e.g., `feat/my-change`).
2. Make changes and run relevant tests.
3. Open a pull request with a clear summary and test plan.

## Creating a new adapter

Choose the base class that matches your datasource:

| Base | Package | Use Case | Dependencies |
| --- | --- | --- | --- |
| `BaseSQLAlchemyAdapter` | `adapter-sqlalchemy` | Relational databases | SQLAlchemy |
| `DatasourceAdapter` (protocol) | `adapter-sdk` | Non-SQL or custom sources | None |

### SQL adapter

Implement `BaseSQLAlchemyAdapter` to inherit schema fetch and execution.

```python
from nl2sql_sqlalchemy_adapter import BaseSQLAlchemyAdapter

class MyDbAdapter(BaseSQLAlchemyAdapter):
    def connect(self, config):
        ...
```

### Non-SQL adapter

Implement the `DatasourceAdapter` protocol directly.

```python
from nl2sql_adapter_sdk import DatasourceAdapter

class MyApiAdapter(DatasourceAdapter):
    ...
```

## Where to look

- Architecture and system behavior: `docs/architecture/`
- Core API reference: `docs/api/core/`
- REST API reference: `docs/api/rest/`
