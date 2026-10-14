# SQLAlchemy Adapter Reference

The **SQLAlchemy Adapter** (`nl2sql.adapters.sqlalchemy_base`, shipped in the `nl2sql` package) provides a helper base class for building adapters for any SQL database supported by SQLAlchemy.

## Base Class: `BaseSQLAlchemyAdapter`

Constructs a robust adapter by wrapping standard SQLAlchemy components.

```python
from nl2sql.adapters.sqlalchemy_base import BaseSQLAlchemyAdapter
```

### Features

| Feature | Description |
| :--- | :--- |
| **Automatic Schema** | Uses `sqlalchemy.inspect` to reflect tables, columns, and foreign keys automatically. |
| **Automatic Stats** | Runs optimized generic SQL queries to fetch `min`, `max`, `null_percentage`, and `distinct_count` (see [Column statistics](#column-statistics)). |
| **Connection Pooling** | Manages engine lifecycle and connection pools. |
| **Transaction Safety** | Implements generic `dry_run` using transaction rollbacks. |
| **Default Schema** | `default_schema()` reports the schema an unqualified table resolves to (`inspect(engine).default_schema_name`); RBAC reads a two-part `datasource.table` policy entry as that table in this schema. |

### Column statistics

`fetch_schema_snapshot(scope)` profiles every column of every table and view
the datasource's [`index` scope](../configuration/datasources.md#index) wants, on one
connection, one column at a time, so one column the database cannot profile
does not fail the datasource's index:

- **Type-aware.** The reflected SQLAlchemy type decides what is asked for.
  `Boolean` and `Uuid` (and the dialect types built on them: Postgres `uuid`,
  SQL Server `bit` and `uniqueidentifier`) get null and distinct counts but no
  `MIN`/`MAX`, which Postgres and SQL Server reject for them. `JSON` (and
  `jsonb`), `LargeBinary` (`bytea`, `blob`), `ARRAY` and any type whose name
  says json, blob, binary, bytea, xml or array get no statistics at all.
  `min_max_skip_reason(type)` in `nl2sql.adapters.sqlalchemy_base.adapter`
  gives the reason. Dates, times, decimals and UUIDs a driver returns for
  `MIN`/`MAX` are stored as ISO strings, floats and text.
- **Isolated.** A statement that fails, for one column, a table's row count
  or a whole table, is caught and the connection's read-only transaction is
  rolled back, so on Postgres the aborted transaction does not fail every
  query after it. A rollback rather than a savepoint per column: profiling only
  reads, so it loses nothing, and it works on every dialect (pysqlite and DuckDB
  savepoints do not) without a round trip per column.
- **Recorded.** A column whose statistics failed keeps its description with
  `statistics: None`; a failed row count is recorded as `0`; a table that could
  not be profiled at all has no column metadata. Each table with failed columns
  logs one warning naming them with the driver's error, and the columns that
  skipped `MIN`/`MAX` by type; a table that only skipped by type logs at debug.

### Required class attributes

`title`, `connection_model` (a `ConnectionModel` subclass), `driver_modules`
(the DBAPI driver your URL names, for example `("psycopg2",)`) and
`install_hint`. The base class sets `interface_version = 2`. See
[Class attributes](sdk.md#class-attributes).

### Required Overrides

#### `url(connection) -> URL | str`

Turns the validated `connection_model` instance into a SQLAlchemy URL. Build it
with `sqlalchemy.URL.create`, so a password holding `@ : / # %` is escaped.

### Optional Overrides

#### `build_engine() -> Engine`

Creates the SQLAlchemy engine from `url(connection)`, on first use: building
the adapter creates none. Override it to pass driver settings (timeouts,
isolation levels).

The base class keeps `limits.timeout_ms` but does not enforce it: SQLAlchemy has
no generic statement timeout and silently ignores an `execution_options["timeout"]`.
Each adapter enforces it in `build_engine()` the way its driver allows: Postgres with
`statement_timeout`, MySQL with `MAX_EXECUTION_TIME`, SQL Server with pyodbc's
query timeout, DuckDB by interrupting the connection. An adapter that does nothing
here has no statement timeout.

#### `close()`

Disposes the connection pool, if one was made. The engine calls an adapter's
`close()` once, from `engine.close()`. Building an adapter never connects: the
engine (and the driver import) is made on the first query or `test_connection()`.

#### `local_path(path) -> str`

For a file-based database. The engine passes each adapter `folder`, the
folder of the `nl2sql.yaml` the datasource came from (None when the config was
built in code). `local_path` reads a relative path from there, and leaves an
absolute path and `:memory:` as they are. The SQLite and DuckDB adapters call it
in `url`.

#### `get_dialect() -> str`

Returns the **sqlglot** dialect name the engine renders SQL in: `postgres`,
`tsql`, `mysql`, `duckdb`, `sqlite`, or any other name
`sqlglot.Dialect.get_or_raise` accepts. It is not the SQLAlchemy dialect name
(`postgresql`, `mssql`), which sqlglot rejects. The base class raises
`NotImplementedError`, so every adapter must implement it.
`tests/adapters/unit/test_adapter_dialect_names.py` checks every adapter
registered under the `nl2sql.adapters` entry point.

#### `explain(sql: str)` / `cost_estimate(sql: str)`

The base class provides stubs. Override these to implement database-specific optimization logic (e.g., `EXPLAIN ANALYZE`).

#### `execute_batches(request, batch_size)`

Implements the SDK's optional `StreamingAdapterProtocol` (see [the SDK](sdk.md#optional-hook-execute_batchesrequest-batch_size)).
It opens the result with `stream_results=True` and yields it in frames of
`fetchmany(batch_size)` rows, each with the columns, so no more than one batch
is held as Python rows. `stream_results` is a server-side cursor on drivers
that have one (psycopg2, the MySQL drivers); the others ignore it and buffer
in the driver, which is still not a Python row list. A `SQLAlchemyError`,
before the first row or between batches, is yielded as `query_failed(exc)`,
the same failed frame `execute` returns. `execute` itself still reads the whole
result with `fetchall`.

#### `is_retryable_error(exc) -> bool`

`execute()` catches every `SQLAlchemyError` the query raises and returns it as
a failed `ResultFrame` (`query_failed(exc)`), with `retryable` from this method
(see [the SDK](sdk.md)). The default reads the driver's SQLSTATE when
there is one: class `42` (syntax or an unknown name) and `22` (a data or type
error) are the query's fault, except `42501`, insufficient privilege. With no
SQLSTATE it falls back on the error's kind: `ProgrammingError` and `DataError`
are retryable unless the message says permission or access was denied;
`OperationalError` (timeouts, connections, locks) is not. The message the
engine sees is the driver's first line, at most 200 characters, prefixed
"The database rejected the query:"; a refusal gets a generic message instead,
and the full error goes to the log.

Override it when your driver reports a query mistake another way. SQLite does:
it raises `OperationalError` with no SQLSTATE for `no such column` as well as
for a read-only database, so `SqliteAdapter` reads the message.
