# Contributing to NL2SQL

Thanks for contributing to the `nl2sql` monorepo. This guide covers local setup,
tests, documentation, and adapter development.

## Monorepo layout

- `packages/nl2sql`: Engine and pipeline, the CLI (`nl2sql.cli`), and the
  database adapters (`nl2sql.adapters.*`, including the SQLAlchemy base).
- `packages/api`: FastAPI REST service.
- `packages/adapter-sdk`: Adapter interfaces and contracts.
- `docs/`: MkDocs documentation.

## Prerequisites

- Python 3.12+ (CI tests 3.12 and 3.13)
- Docker (required for integration tests that spin up databases)

## Local setup

1. Clone the repository.
2. Create and activate a virtual environment on a supported interpreter.
3. Install the three packages in editable mode, plus the test tooling.

Example (PowerShell), using a 3.13 interpreter:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install `
    -e packages/adapter-sdk -e "packages/nl2sql[all]" -e packages/api `
    pytest pytest-randomly httpx
```

Swap `[all]` for a narrower extra (`[postgres]`, `[duckdb]`, ...) if you only
need one dialect. `httpx` is listed explicitly because `packages/api` tests use
`fastapi.testclient.TestClient`, which needs it; it currently also arrives
transitively through the engine's dependencies, so naming it just makes the
requirement intentional rather than accidental.

## Running tests

Install the dev tooling (PEP 735 dependency group) first:

```bash
python -m pip install --group dev
```

Unit tests:

```bash
pytest packages/nl2sql/tests/unit
```

`pytest-randomly` shuffles test order on every run, so tests that depend on the
order they run in fail locally instead of only in CI. Reproduce a failing run
with the seed it prints: `pytest -p randomly --randomly-seed=<seed>`.

Integration tests need generated demo data, and some modules also need a real
LLM API key. The two are separate markers, so the key-free subset -- the same
selection CI runs -- can be run on its own:

```bash
nl2sql setup --demo --lite
EMBEDDING_PROVIDER=local pytest -m "integration and not llm"
```

Add `--collect-only -q` to that command to see exactly which tests the subset
covers; the count moves as tests are added, so read it from pytest rather than
from this page.

Add a key to run the rest:

```bash
EMBEDDING_PROVIDER=local OPENAI_API_KEY=sk-... pytest -m integration
```

The `llm`-marked tests fail, rather than skip, without `OPENAI_API_KEY`. That
is expected, not a regression.

See `docs/testing/architecture.md` for what each marker means.

### Troubleshooting: stale bytecode after moving a checkout

If tests fail in a checkout you moved or renamed, and the traceback shows bare
`>   ???` lines or file paths pointing at the *old* checkout directory, the
cause is stale `__pycache__` bytecode that still records the previous paths --
not the code you are looking at. Clear the caches and re-run:

```bash
find . -name "__pycache__" -type d -prune -exec rm -rf {} +
rm -rf .pytest_cache
```

PowerShell equivalent:

```powershell
Get-ChildItem -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
Remove-Item -Recurse -Force .pytest_cache -ErrorAction SilentlyContinue
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

## Releasing

All three packages share one version and are released together. The checklist --
bumping the versions and what publishing a GitHub release triggers -- is in
`docs/development/releasing.md`.

## Creating a new adapter

Choose the base class that matches your datasource:

| Base | Package | Use Case | Dependencies |
| --- | --- | --- | --- |
| `BaseSQLAlchemyAdapter` | `nl2sql.adapters.sqlalchemy_base` | Relational databases | SQLAlchemy |
| `DatasourceAdapter` (protocol) | `adapter-sdk` | Non-SQL or custom sources | None |

### SQL adapter

Implement `BaseSQLAlchemyAdapter` to inherit schema fetch and execution.

```python
from nl2sql.adapters.sqlalchemy_base import BaseSQLAlchemyAdapter

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
