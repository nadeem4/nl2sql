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

- Python 3.9+
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

Install the dev tooling (PEP 735 dependency group) first:

```bash
python -m pip install --group dev
```

Unit tests:

```bash
pytest packages/core/tests/unit
```

`pytest-randomly` shuffles test order on every run, so tests that depend on the
order they run in fail locally instead of only in CI. Reproduce a failing run
with the seed it prints: `pytest -p randomly --randomly-seed=<seed>`.

Integration tests need generated demo data, and four modules also need a real
LLM API key. The two are separate markers, so the key-free subset -- 29 tests,
the same ones CI runs -- is selectable on its own:

```bash
nl2sql setup --demo --lite
EMBEDDING_PROVIDER=local pytest -m "integration and not llm"
```

Add a key to run the rest:

```bash
EMBEDDING_PROVIDER=local OPENAI_API_KEY=sk-... pytest -m integration
```

See `docs/testing/architecture.md` for what each marker means.

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

## Releasing

All nine packages share one version and are released together. The checklist --
bumping the versions, the drift check, and what publishing a GitHub release
triggers -- is in `docs/development/releasing.md`.

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
