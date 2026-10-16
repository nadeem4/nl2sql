# Building Adapters Guide

The NL2SQL Platform is designed to be extensible. You can build adapters for any datasource, from SQL databases to REST APIs.

## Implementation Path

There are two primary ways to build an adapter. Choose the one that fits your target:

| If you are checking... | Use... | Reference |
| :--- | :--- | :--- |
| A standard SQL Database (Postgres, Oracle, Snowflake) | `nl2sql_sdk.sqlalchemy` (`pip install "nl2sql-sdk[sqlalchemy]"`) | **[SQLAlchemy Adapter Reference](sqlalchemy.md)** |
| A NoSQL DB, REST API, or custom driver | Core adapter protocol | **[Adapter Interface Reference](sdk.md)** |

## Option 1: The "Fast Lane" (SQLAlchemy)

For 95% of use cases, you are connecting to a SQL database that already has a Python SQLAlchemy dialect.

**Use `SQLAlchemyAdapter`** from the SDK. It handles:

* Connecting on first use, with a connection pool (`SQLAlchemyConnection` adds `pool`)
* Executing queries with bound parameters, and turning a database error into a failed result
* Reading the schema: one reflection pass per schema, filtered by the datasource's `index` scope
* Catalog row estimates, type-aware statistics, and values only when `index.sample_values` is on
* Reporting what a table or column was indexed without as an `IndexIssue`
* `probe_overlap`, the schema profiler's join check, in SQLAlchemy Core

### Example

A complete adapter: the built-in PostgreSQL one, less its `session_setup`, `classify` and `capabilities`.

```python
from typing import Dict, Literal

from pydantic import Field, SecretStr
from sqlalchemy import URL, text

from nl2sql_sdk.sqlalchemy import SQLAlchemyAdapter, SQLAlchemyConnection


class PostgresConnection(SQLAlchemyConnection):
    host: str = Field(title="Host", examples=["db.internal"])
    port: int = Field(5432, title="Port")
    database: str = Field(title="Database")
    user: str = Field(title="User")
    password: SecretStr = Field(title="Password")
    sslmode: Literal["disable", "prefer", "require", "verify-full"] = Field(
        "prefer", title="SSL mode", json_schema_extra={"group": "advanced"})


class PostgresAdapter(SQLAlchemyAdapter):
    title = "PostgreSQL"
    connection_model = PostgresConnection
    driver_modules = ("psycopg2",)
    install_hint = 'pip install "nl2sql-engine[postgres]"'

    def url(self, connection: PostgresConnection) -> URL:
        # URL.create escapes the credentials; an f-string breaks on a password
        # containing @ : / # or %.
        return URL.create("postgresql+psycopg2", username=connection.user,
                          password=connection.password.get_secret_value(), host=connection.host,
                          port=connection.port, database=connection.database,
                          query={"sslmode": connection.sslmode})

    def get_dialect(self) -> str:
        return "postgres"  # a sqlglot dialect name, not SQLAlchemy's "postgresql"

    def system_schemas(self) -> set[str]:
        return {"pg_catalog", "information_schema"}

    def row_estimates(self, conn, schema: str) -> Dict[str, int]:
        rows = conn.execute(text(
            "SELECT c.relname, c.reltuples FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relkind IN ('r', 'p', 'm', 'f') AND c.reltuples >= 0"
        ), {"schema": schema})
        return {name: int(count) for name, count in rows}
```

Make the session read-only and enforce `limits.timeout_ms` in
`session_setup()`: the base class keeps the limit but does not apply it. Then
declare `ENFORCES_READ_ONLY` and `ENFORCES_TIMEOUT` for what it enforces, and
override `classify()` to map your driver's connection errors to stages (see
the [reference](sqlalchemy.md#session_setupdbapi_connection)).

### Adding `estimate`

The engine's cost rule asks an adapter for the rows a query will *read*
before it runs it. Reading a plan is the database's own, so the base has no
`estimate`: add one and declare `ESTIMATES_COST`
([the contract](sdk.md#optional-hooks-estimate-and-probe_overlap)).

1. Ask for the plan without running the query (`EXPLAIN`, an estimated
   plan), on the adapter's own connections, so it runs read-only and under
   `timeout_ms`, with `parameters` bound as `execute` binds them.
2. Add up the rows each scan operator *reads*. A full scan counts its
   table's row count, never the operator's own row estimate, which is its
   output after the filter: a scan that filters a billion rows down to ten
   reads a billion. An index seek counts its own estimate. Both are per
   execution: an operator on the inner side of a nested loop runs once per
   outer row, so multiply by the times it runs where the plan says.
3. Return `Estimate(rows=..., cost=..., source=...)`: `cost` is the
   planner's total, for display only, or None when the plan has none;
   `source` says what was read.
4. Raise `RuntimeError` on every failure, the database's (with the
   driver's first line, `driver_message`) and a plan you cannot read
   alike: the engine's cost rule reads a `RuntimeError` as "no estimate",
   and the kit checks it. Never fall back to the operator's filtered
   estimate, and never report a statement the database gives no plan for
   as zero rows: no plan is no estimate, so raise.
5. Leave no session setting behind. A setting that changes what a query
   returns (SQL Server's `SHOWPLAN_XML`) must be turned off before the
   connection is closed: a driver's own pool can hand the session on.

The built-in PostgreSQL adapter's:

```python
def estimate(self, sql: str, parameters: Dict[str, Any]) -> Estimate:
    try:
        with self.engine.connect() as conn:
            plan = _plan(self._run(conn, f"EXPLAIN (FORMAT JSON, VERBOSE) {sql}", parameters).scalar())
            seq_scans, rows = scans(plan)  # each Seq Scan with the times it runs; index scans' rows
            sizes = {(schema, name): self._planned_size(conn, schema, name) for schema, name, _ in seq_scans}
        rows += sum(sizes[(schema, name)] * times for schema, name, times in seq_scans)
        cost = plan.get("Total Cost")
        return Estimate(rows=int(rows), cost=float(cost) if cost is not None else None,
                        source="EXPLAIN (FORMAT JSON) and pg_class.reltuples")
    except SQLAlchemyError as exc:
        raise RuntimeError(f"{self} could not estimate the query: {driver_message(exc)}") from None
    except _UNREADABLE as exc:  # KeyError, TypeError, ValueError, AttributeError, IndexError
        raise RuntimeError(f"{self} could not estimate the query: the plan could not be read "
                           f"({type(exc).__name__})") from None
```

> See the **[SQLAlchemy Adapter Reference](sqlalchemy.md)** for full API details.

## Option 2: The "Custom" Path (Protocol)

If you need to connect to something else (e.g., ElasticSearch, a CRM API, or a raw SQL driver), implement the core adapter protocol.

**Implement `DatasourceAdapter`**. You must manually handle:

* Fetching and normalizing schema metadata.
* Executing queries and formatting results.
* Declaring a `connection_model`: a `ConnectionModel` subclass with one field per connection setting. The engine validates the datasource's `connection` against it (unknown keys are errors) and passes the instance to your constructor.
* The constructor the engine builds every adapter with: `adapter_cls(datasource_id, connection, limits, *, folder=None)`, where `limits` is the datasource's `Limits` and `folder` the folder of `nl2sql.yaml`, or None. Do not connect in the constructor: `engine.check()` calls `test_connection()`, which returns a `ConnectionCheck`.
* A `close()` that releases connections; `engine.close()` calls it once.
* Declaring `interface_version = 2`, a `title`, the `driver_modules` it imports to connect, and an `install_hint` (see [Class attributes](sdk.md#class-attributes)).
* A `default_schema()` naming the schema an unqualified table resolves to, or returning None. Policies read a table with no schema as a table in this schema; without one, `main` is assumed.
* A `fetch_schema_snapshot(scope, on_table=None)` that reads only what the datasource's `IndexScope` wants (`scope.wants_table`, `scope.wants_column`).

### Example

```python
from pydantic import SecretStr

from nl2sql_sdk import (AdapterRequest, ConnectionCheck, ConnectionModel, IndexScope, Limits,
                        ResultFrame, SchemaSnapshot)


class MyApiConnection(ConnectionModel):
    url: str
    token: SecretStr


class MyApiAdapter:
    interface_version = 2
    title = "My API"
    connection_model = MyApiConnection
    driver_modules = ("httpx",)
    install_hint = 'pip install "nl2sql-my-api"'

    def __init__(self, datasource_id: str, connection: MyApiConnection, limits: Limits, *, folder=None):
        self.datasource_id, self.connection, self.limits = datasource_id, connection, limits

    def capabilities(self):
        return set()  # not SQL: the SQL agent does not serve it

    def test_connection(self) -> ConnectionCheck:
        ...  # call the API; return ConnectionCheck(ok=False, message=...) on failure

    def fetch_schema_snapshot(self, scope: IndexScope, on_table=None) -> SchemaSnapshot:
        ...  # call the API, return the schema of what scope.wants_table() keeps

    def execute(self, request: AdapterRequest) -> ResultFrame:
        ...  # run the request, return rows

    def get_dialect(self) -> str: ...
    def default_schema(self): return None
    def close(self): pass
```

> See the **[Adapter Interface Reference](sdk.md)** for the method signatures and compliance guide.

## Running the conformance kit

Whichever path you choose, run the conformance kit against a real database
before you publish. It is a pytest test class, `AdapterConformance`, in
`nl2sql_sdk.testing`:

```bash
pip install "nl2sql-sdk[testing]"   # adds pytest and sqlglot
```

Subclass it in a test module (the name must start with `Test`) and give it
four fixtures:

| Fixture | What it gives |
| :--- | :--- |
| `connection` | The `ConnectionModel` of a database the tests may write the kit's seed into |
| `adapter_factory` | `adapter_factory(connection, limits)`: your adapter, built the way the engine builds it |
| `seed` | `seed(statements)`: runs each SQL statement with a *writable* connection, outside your adapter |
| `sleep_sql` | `sleep_sql(seconds)`: a query that runs at least that long |

```python
import pytest
from sqlalchemy import create_engine

from nl2sql_sdk.testing import AdapterConformance

from my_adapter import MyAdapter, MyConnection


class TestMyAdapterConformance(AdapterConformance):

    @pytest.fixture
    def connection(self):
        return MyConnection(host="127.0.0.1", database="kit", user="kit", password="...")

    @pytest.fixture
    def adapter_factory(self):
        return lambda connection, limits: MyAdapter("kit", connection, limits)

    @pytest.fixture
    def seed(self):
        admin = create_engine("mydb://admin:...@127.0.0.1/kit")

        def run(statements):
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                for statement in statements:
                    conn.exec_driver_sql(statement)
        return run

    @pytest.fixture
    def sleep_sql(self):
        return lambda seconds: f"SELECT pg_sleep({seconds})"
```

Each test writes the kit's seed first and drops it after: two schemas
(`kit_a`, `kit_b`), a view, a mixed-case table, boolean, uuid and json
columns, and `kit_orders`, the largest table, with 100 rows. It is written once and transpiled to your `get_dialect()` with
sqlglot. Two class attributes adapt it:

- `schemas = ("main",)` for a database with one schema, such as SQLite: the
  seed then creates no schema, and the check that needs two is skipped.
- `column_types = {"uuid": "CHAR(36)"}` for a database without one of the
  three types (MySQL has no uuid, SQL Server 2022 no json).

A database with logins can also give `limited_login`: the `ConnectionModel`
of a second login whose password holds `@ : / # %` and that can read
`kit_a` but not `kit_b`. It may request the `seeded` fixture to grant on the
seed. Without it, the two checks that need it are skipped.

| Check | Passes when |
| :--- | :--- |
| Dialect | `get_dialect()` is a name `sqlglot.Dialect.get_or_raise` accepts |
| Connection model | An unknown key fails; a password with `@ : / # %` connects; no secret appears in `repr`, `str`, a validation error or a failed check |
| Stages | Good settings are `ok`; a wrong password is `auth`, a closed port `network`, a missing database `database` (skipped when the model has no such field) |
| Scope | System schemas stay out even when named; include and exclude rules hold; views are included; mixed-case names keep their case |
| Odd columns | Profiling the boolean, uuid and json columns gives statistics or an `IndexIssue`, never an exception |
| Row estimates | Every table has a row count and says where it came from |
| Bound parameters | `1; DROP TABLE kit_people` passed as a parameter is a value |
| Read-only | If `ENFORCES_READ_ONLY` is declared, an `INSERT` and a `CREATE TABLE` through `execute` fail, are not retryable and change nothing |
| Timeout | If `ENFORCES_TIMEOUT` is declared, `sleep_sql(5)` with a one-second limit fails within three seconds as `QUERY_TIMEOUT` |
| Errors | A missing column is `retryable`; a permission error is not |
| Streaming | If the adapter has `execute_batches`, its frames match `execute`'s result |
| Estimates | If `ESTIMATES_COST` is declared: `estimate` returns `rows >= 0` and a `source`; an estimate of `sleep_sql(5)` returns or raises `RuntimeError` within two seconds (it does not run the statement; a statement with no plan, such as SQL Server's `WAITFOR`, raises); a filter on an unindexed column of `kit_orders` that matches no row reports at least half the table's rows; a missing table raises `RuntimeError` |
| Overlap | If the adapter has `probe_overlap`: `kit_orders.person_id` against `kit_people.id` samples 2 distinct values and matches 2; `kit_people.name` against `KitMixedCase.DisplayName` matches none |

A capability you do not declare is not checked: its test is reported as
skipped, with the reason, so `pytest -rs` shows what your adapter does not
promise. The five built-in adapters run the kit: SQLite and DuckDB in
`packages/nl2sql/tests/adapters/unit/test_conformance.py`, PostgreSQL, MySQL
and SQL Server in `packages/nl2sql/tests/realdb/test_conformance.py`.
