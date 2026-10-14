# Contributing to NL2SQL

Thanks for contributing to the `nl2sql` monorepo. This guide covers local setup,
tests, documentation, and adapter development.

The rules a change has to keep -- the package boundaries, where dialect
knowledge lives, and what the model is allowed to emit -- are in `CLAUDE.md` at
the repo root, one line each, with the reasoning in
`docs/architecture/invariants.md`. Most are enforced by
`packages/nl2sql/tests/architecture/test_boundaries.py`, so a broken boundary
fails the suite rather than review.

## Monorepo layout

- `packages/nl2sql`: Engine and pipeline, the CLI (`nl2sql.cli`), and the
  database adapters (`nl2sql.adapters.*`, including the SQLAlchemy base).
- `packages/api`: FastAPI REST service.
- `packages/sdk`: the SDK (`nl2sql-sdk`, imported as `nl2sql_sdk`): the interfaces an extension implements, today the adapter contracts.
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
    -e packages/sdk -e "packages/nl2sql[postgres,duckdb,demo]" -e packages/api
.\.venv\Scripts\python.exe -m pip install --group dev
```

The full suite is green only with those extras. `postgres` and `duckdb` supply
the drivers the adapter tests import; `demo` supplies the FastAPI/uvicorn server
behind `nl2sql demo`. Swap in `[all]` if you want every dialect driver — but
note `all` covers adapters only, not `demo`, `aws`, `azure` or `hashicorp`.
There is deliberately no `sqlite` extra: that driver is in the standard library.

The second command installs the PEP 735 `dev` group (`pip` 25.1 or newer):
`pytest`, `pytest-randomly`, `pytest-timeout`, and `httpx` — the last because
`packages/api` tests drive the app through `fastapi.testclient.TestClient`,
which imports it, and no distribution in this repo declares it.

## Running tests

Unit tests:

```bash
pytest packages/nl2sql/tests/unit
```

`pytest-randomly` shuffles test order on every run, so tests that depend on the
order they run in fail locally instead of only in CI. Reproduce a failing run
with the seed it prints: `pytest -p randomly --randomly-seed=<seed>`.

The package boundaries are their own suite, an AST scan of `packages/*/src`
that imports none of it:

```bash
pytest packages/nl2sql/tests/architecture
```

Integration tests run on the demo databases packaged with the engine, and
build their configs (`nl2sql.testing.config.make_demo_config`, local
embeddings) themselves; some modules also need a real LLM API key. The two are
separate markers, so the key-free subset -- the same selection CI runs -- can
be run on its own:

```bash
pytest -m "integration and not llm"
```

The first run downloads the ~79 MB local embedding model.

Add `--collect-only -q` to that command to see exactly which tests the subset
covers; the count moves as tests are added, so read it from pytest rather than
from this page.

Add a key to run the rest:

```bash
OPENAI_API_KEY=sk-... pytest -m integration
```

The `llm`-marked tests fail, rather than skip, without `OPENAI_API_KEY`. That
is expected, not a regression.

### Real databases

Tests marked `realdb` run against real PostgreSQL, MySQL and SQL Server
servers: the test bed, one compose file under `testbed/`. With Docker
running:

```bash
python testbed/passwords.py > testbed/.env               # once: random passwords, ignored by version control
docker compose -f testbed/docker-compose.yml up -d --wait
set -a; . testbed/.env; set +a
REALDB=compose pytest -m realdb
docker compose -f testbed/docker-compose.yml down        # add -v to drop the data
```

The first `up` downloads the sample databases and loads them, which takes
several minutes; later ones reuse the data volumes. Without the test bed the
`realdb` tests skip with that fix in the message. CI runs them in the
`realdb` workflow when a pull request changes database-facing code. The
owner can also run them on the test bed's GCP VM with
`python scripts/testbed.py run -- pytest -m realdb`, which always stops the
VM afterwards. See `docs/testing/test-bed.md`.

See `docs/testing/architecture.md` for what each marker means.

### Checking a config

`nl2sql config check` checks a whole config without starting the engine and
exits 1 on any problem (see `docs/configuration/system.md`). CI's integration
job runs it with `--connect` on the config `nl2sql init --demo` writes, before
indexing, and fails if it created anything under `storage.path`. Run the same
locally after changing the config models, the registry or `init`:

```bash
nl2sql init --demo /tmp/demo-check
nl2sql --config /tmp/demo-check/nl2sql.yaml config check --connect
```

Your own deployment's CI can run it the same way, against its `nl2sql.yaml`.

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

Every release needs a page at `docs/releases/X.Y.Z.md`. When the release-please
PR is open, run the `release-notes` skill (`.claude/skills/release-notes/`). It
writes the page and opens a `docs(release): notes for X.Y.Z` PR. Merge that PR
first. The `release-notes` status on the release PR then turns green, and the
owner merges the release PR.

## Creating a new adapter

Choose the base class that matches your datasource:

| Base | Package | Use Case | Dependencies |
| --- | --- | --- | --- |
| `BaseSQLAlchemyAdapter` | `nl2sql.adapters.sqlalchemy_base` | Relational databases | SQLAlchemy |
| `DatasourceAdapter` | `nl2sql_sdk` | Non-SQL or custom sources | None |

### SQL adapter

Implement `BaseSQLAlchemyAdapter` to inherit schema fetch and execution.

```python
from nl2sql.adapters.sqlalchemy_base import BaseSQLAlchemyAdapter

class MyDbAdapter(BaseSQLAlchemyAdapter):
    title = "My DB"
    connection_model = MyDbConnection  # a nl2sql_sdk.ConnectionModel subclass

    def url(self, connection):
        ...
```

### Non-SQL adapter

Implement the `DatasourceAdapter` protocol directly (see `docs/adapters/sdk.md`).

```python
from nl2sql_sdk import DatasourceAdapter

class MyApiAdapter(DatasourceAdapter):
    ...
```

## Where to look

- Architecture and system behavior: `docs/architecture/`
- Core API reference: `docs/api/core/`
- REST API reference: `docs/api/rest/`
