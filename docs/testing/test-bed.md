# The real-database test bed

Most tests run on SQLite, DuckDB and mocks. Anything that depends on how a
real server behaves (catalogs, quoting, permissions, read-only sessions,
timeouts) is tested against real servers instead: PostgreSQL, MySQL and SQL
Server, each loaded with a well-known sample database and a large generated
schema. One compose file, `testbed/docker-compose.yml`, defines all of it,
and the same file runs on your machine and on GitHub's runners in CI.

The design is in [Phase 2, section 3](../architecture/phase2.md#3-the-real-database-test-bed).

| Server | Sample database | Generated wide schema |
|---|---|---|
| PostgreSQL 16 | Pagila, in database `pagila` | database `wide`, schemas `sales_01` ... `ops_20` and `audit` |
| MySQL 8.0 | Sakila, in database `sakila` | databases `wide_s01` ... `wide_s20` and `wide_audit` (MySQL's schemas are databases) |
| SQL Server 2022 | AdventureWorksLT, in database `AdventureWorksLT2022` | database `wide`, schemas `sales_01` ... `ops_20` and `audit` |

## Local use

You need Docker with the compose plugin.

```bash
python testbed/passwords.py > testbed/.env               # once: random passwords, ignored by version control
docker compose -f testbed/docker-compose.yml up -d --wait
set -a; . testbed/.env; set +a                           # the tests read the same variables
REALDB=compose pytest -m realdb
docker compose -f testbed/docker-compose.yml down        # add -v to drop the data
```

In PowerShell, load the variables with
`Get-Content testbed/.env | ForEach-Object { $name, $value = $_ -split '=', 2; Set-Item "env:$name" $value }`
and set `$env:REALDB = "compose"`.

The compose file has no default password, so no password is written in the
repository: `TESTBED_POSTGRES_PASSWORD`, `TESTBED_MYSQL_PASSWORD` and
`TESTBED_MSSQL_PASSWORD` are required, and `up` stops naming the variable if one is
missing. `testbed/passwords.py` prints three fresh random ones. Docker Compose
reads `testbed/.env` by itself, because it sits beside the compose file. A
server keeps the password its data volume was created with, so make a new
`.env` only together with `down -v`.

The first `up` downloads the samples, generates the wide schema and loads
everything, which takes a few minutes (SQL Server wants about 2 GB of
memory). Loading happens once per data volume, so a later `up` is quick.
`--wait` returns only when every server is loaded.

Install the drivers the tests use with
`pip install -e "packages/nl2sql[postgres,mysql,mssql,duckdb]"`. SQL Server
also needs Microsoft's ODBC Driver 18 for SQL Server on the machine.

## The compose file

| Service | Image | What it does |
|---|---|---|
| `samples` | `python:3.12-slim` | One-shot. Runs `testbed/samples.py`: downloads each sample from a pinned URL, checks its SHA-256, extracts Sakila from its archive, and writes the wide schema for the three servers with `testbed/wide_schema.py`, all into the `samples` volume. Skips what is already there. |
| `postgres` | `postgres:16` | Starts after `samples`. On its first start, `testbed/postgres/init.sh` (the image's own init mechanism) loads Pagila and the wide schema and creates the reader login. |
| `mysql` | `mysql:8.0` | The same with Sakila, through `testbed/mysql/init.sh`. The `mysql` client handles Sakila's `DELIMITER` blocks. |
| `mssql` | `mcr.microsoft.com/mssql/server:2022-latest` | The server. Its healthcheck passes only once `mssql-init` has finished. |
| `mssql-init` | the same image, for its `sqlcmd` | One-shot. Runs `testbed/mssql/init.sh`: restores AdventureWorksLT from the `.bak`, loads the wide schema and creates the reader login. Skips each database that already exists. |

Every server has a healthcheck that passes only once it is loaded, so
`up --wait` is enough before running tests.

The file reads four variables:

| Variable | Default |
|---|---|
| `TESTBED_BIND` | `127.0.0.1` |
| `TESTBED_POSTGRES_PASSWORD` | none: required (`testbed/passwords.py`) |
| `TESTBED_MYSQL_PASSWORD` | none: required |
| `TESTBED_MSSQL_PASSWORD` | none: required |

| Service | Published port | Admin user | Sample database |
|---|---|---|---|
| `postgres` | 15432 | `postgres` | `pagila` |
| `mysql` | 13306 | `root` | `sakila` |
| `mssql` | 11433 | `sa` | `AdventureWorksLT2022` |

The ports avoid a clash with a database already installed locally.

Each server also has a login `nl2sql_reader`, with the server's password,
that may read its sample database and nothing else: not the wide schema.
Tests use it where a limited login matters.

## The sample databases and their licences

Nothing is committed to the repository; the `samples` service downloads each
file and refuses one whose SHA-256 differs from the pinned value, so we never
redistribute the samples. If one is ever vendored, its licence file goes
beside it.

| Sample | Files | Source | Licence |
|---|---|---|---|
| Pagila | `pagila-schema.sql`, `pagila-insert-data.sql` | github.com/devrimgunduz/pagila, pinned to commit `5ba5a57`, from before Pagila moved to PostgreSQL 18 | PostgreSQL License (copyright Devrim Gündüz) |
| Sakila | `sakila-schema.sql`, `sakila-data.sql`, from `sakila-db.tar.gz` | downloads.mysql.com | New BSD |
| AdventureWorksLT 2022 | `AdventureWorksLT2022.bak` | github.com/microsoft/sql-server-samples, release `adventureworks` | MIT |

The server images have their own terms. The SQL Server image runs the free
Developer edition, which needs `ACCEPT_EULA=Y` and is licensed for
development and testing, which is all we do with it.

When an upstream file changes, `samples` stops with the expected and actual
hashes. Check the new file, then pin its URL and hash in `testbed/samples.py`.

## The generated wide schema

`python testbed/wide_schema.py --dialect postgres|mysql|tsql --seed 7` writes
one SQL file. The same seed always gives the same schema:

| Feature | Count | What it tests |
|---|---|---|
| Schemas | 20 (`sales_01` ... `ops_20`), plus a small `audit` | Index scope, policy globs |
| Tables | 1,200 in the 20 schemas, with 5 to 30 columns each | Reflection and retrieval at scale |
| Views | 2 per schema | Views are indexed |
| Odd column types | boolean, uuid, json, binary, xml and array where the server has the type; the nearest plain type where it does not | Profiling never aborts |
| Comments | on about 30% of tables and columns | Descriptions come from the database |
| Foreign keys | within each schema | Join paths |
| Mixed-case names | 10 tables, such as `CustomerAccounts` | Quoting |
| Rows | 3 tables with 1,000,000 rows, 50 with 1,000, the rest empty | Row estimates, profiling limits |

The rows are made on the server (`generate_series` on PostgreSQL, a cross
join of digit tables on MySQL and SQL Server), so the file stays small.

## The `realdb` marker and fixtures

Every test under `packages/nl2sql/tests/realdb/` is marked `realdb`. Its
fixtures find the servers from the environment:

| Variable | Meaning |
|---|---|
| `REALDB=compose` | The compose servers on `127.0.0.1`, at the published ports, with the passwords in the three `TESTBED_*_PASSWORD` variables |
| `REALDB_POSTGRES`, `REALDB_MYSQL`, `REALDB_MSSQL` | A URL for one server, such as `postgresql://postgres:<password>@127.0.0.1:15432/`, with the password percent-encoded. It wins over `REALDB=compose`. |
| `REALDB_REQUIRED=1` | A missing server is a failure, not a skip |

Without a server, a `realdb` test skips with the fix in its message. Under
`REALDB_REQUIRED=1` it fails instead: a green run that skipped the real
databases proves nothing.

| Fixture or helper | What it gives |
|---|---|
| `server` | Each of the three servers in turn (`RealServer`: its admin URL, its sample database, `engine()` for a SQLAlchemy engine) |
| `datasource(server, database=None, **fields)` | A `Datasource` for one database on the server, the sample by default |
| `engine_on(database=None, **fields)` | An `NL2SQL` engine on one database of `server`, with a fake LLM and no key |

Test code may name dialects and use SQLAlchemy directly; the boundary tests
cover `packages/*/src`, not the tests.

## CI: the `realdb` workflow

`.github/workflows/realdb.yml` runs the test bed on GitHub's runners. No
cloud credentials are involved.

| Job | Runs | What it does |
|---|---|---|
| `changes` | always | Works out whether a pull request touches database-facing code (the paths below). On the nightly and on-demand runs the answer is always yes. |
| `realdb` | on a pull request when `changes` says so; nightly on `main`; on demand (`workflow_dispatch`), which is run before every release | generates fresh passwords with `testbed/passwords.py` into the job's environment, masked in the log; `docker compose ... up -d --wait`, installs Microsoft's `msodbcsql18` for pyodbc and `packages/nl2sql[postgres,mysql,mssql,duckdb]`, then `pytest -m realdb` with `REALDB=compose` and `REALDB_REQUIRED=1` |
| `realdb-gate` | always, after the other two | Passes when `realdb` passed, or was skipped because nothing database-facing changed; fails otherwise. The decision is `testbed/gate.py`. This is the check to require. |

The paths that make `realdb` run on a pull request:

```text
packages/sdk/**
packages/nl2sql/src/nl2sql/adapters/**
packages/nl2sql/src/nl2sql/indexing/**
packages/nl2sql/src/nl2sql/storage/**
packages/nl2sql/src/nl2sql/schema/**
packages/nl2sql/tests/realdb/**
testbed/**
.github/workflows/realdb.yml
```

To run it by hand: Actions, "Real databases", "Run workflow", or
`gh workflow run realdb.yml --ref main`.

The unit job in `test.yml` runs `pytest -m "not integration and not realdb"`.

The scripts under `testbed/` have their own unit tests, which need no
database: `packages/nl2sql/tests/unit/test_testbed.py` (the wide schema, the
checksums, skipping what is there, and the gate) and
`packages/nl2sql/tests/unit/test_realdb_fixtures.py` (the skip, the failure
under `REALDB_REQUIRED=1`, and the variables).
