# SQLAlchemy Adapter Reference

`nl2sql_sdk.sqlalchemy` is the base for an adapter to any database SQLAlchemy
reaches. It ships in the SDK as an extra, so an adapter author installs the SDK,
not the engine:

```bash
pip install "nl2sql-sdk[sqlalchemy]"
```

```python
from nl2sql_sdk.sqlalchemy import SQLAlchemyAdapter, SQLAlchemyConnection
```

The five built-in adapters (PostgreSQL, MySQL, SQL Server, SQLite, DuckDB) are
built on it. It is the one part of the SDK that imports SQLAlchemy; the SDK's
core still imports only pydantic and the standard library.

An adapter on it implements `url` and `get_dialect`, and overrides what its
database does differently. [Building adapters](development.md#option-1-the-fast-lane-sqlalchemy)
has a complete one; the built-in PostgreSQL adapter
(`packages/nl2sql/src/nl2sql/adapters/postgres/adapter.py`) is about 60 lines.

## What the base does

| Feature | Description |
| :--- | :--- |
| **Engine on first use** | Building the adapter never connects. The SQLAlchemy engine, and the driver import, are made on the first query or `test_connection()`. |
| **Connection pool** | `SQLAlchemyConnection` adds `pool` (below). |
| **Execution** | `execute` and `execute_batches`, with `AdapterRequest.parameters` bound by the driver, never written into the SQL. A database error is a failed `ResultFrame`, not an exception. |
| **Schema reading** | `fetch_schema_snapshot(scope)`: one reflection pass per schema, filtered by the datasource's [`index` scope](../configuration/datasources.md#index) (below). |
| **Default schema** | `default_schema()` reports the schema an unqualified table resolves to (`inspect(engine).default_schema_name`); policies read a table with no schema as a table in this schema. |

### `pool`

A connection model built on `SQLAlchemyConnection` has a `pool` block, in the
form's advanced group:

| Field | Default | Meaning |
|---|---|---|
| `size` | 5 | Connections kept open |
| `max_overflow` | 0 | Extra connections allowed under load |
| `timeout_s` | 30 | How long a query waits for a free connection |
| `recycle_s` | 1800 | A connection older than this is replaced |

With no overflow, `size` is also the most queries NL2SQL runs against the
database at once, so it cannot overload a production server. A file database
has no server to protect: the SQLite and DuckDB connections are plain
`ConnectionModel`s, with no `pool`.

### Reading a schema

`fetch_schema_snapshot(scope, on_table=None)`:

1. **The scope first.** The database's schemas are listed, and a schema in
   `system_schemas()` or one the scope does not want (`scope.wants_schema`) is
   dropped before any query names it. So is a table the scope does not want.
   An excluded schema costs nothing.
2. **Reflect once.** Each remaining schema is reflected with
   `inspector.get_multi_columns`, `get_multi_pk_constraint`,
   `get_multi_foreign_keys` and `get_multi_table_comment`, for the wanted
   tables and views together. A dialect whose multi-table reflection fails is
   read one table at a time; a table that still cannot be read is an issue
   (below), and is left out when its columns cannot be read.
3. **Row counts from the catalog.** `row_estimates(conn, schema)` gives each
   table's estimate (`row_count_source: catalog`). Only a table with none is
   counted, with `COUNT(*)`, and only under `profile: stats`
   (`row_count_source: exact`). Under `profile: catalog` it has no row count.
4. **Statistics**, under `profile: stats`, one query per column: its null
   share, and what `column_profile(type)` allows: `MIN`/`MAX` and the
   distinct count. A table estimated above `max_profile_rows` is not
   profiled, and gets an issue saying so.
5. **Values**, only with `index.sample_values: true` and only for a column
   whose profile allows them (text): every distinct value when there are 50 or
   fewer, otherwise the five most frequent, most frequent first and ties by
   value. Off by default: values reach the index, the prompts and the model
   provider.

`on_table` is called with each table's `schema.table` as it is profiled.

### Issues, not log lines

A statement that fails while reading a table (its reflection, its row count, a
column's statistics or values) is rolled back, so on PostgreSQL the aborted
transaction does not fail every query after it, and becomes an `IndexIssue` on
the snapshot:

| Field | Value |
|---|---|
| `table` | `schema.table`, or `schema.*` when the schema's row estimates failed |
| `column` | the column, for a statistics or values failure |
| `stage` | `reflect`, `estimate`, `profile` or `values` |
| `message` | the driver's first line, or why the table was not profiled |

The column keeps its description with `statistics: None`. `nl2sql index`
prints each datasource's issues, and `engine.index.build()` returns them in
the datasource's stats under `issues`.

A rollback rather than a savepoint per column: reading only reads, so it loses
nothing, and it works on every dialect (pysqlite and DuckDB savepoints do not)
without a round trip per column.

## The extension surface

### Required

#### `url(connection) -> URL | str`

Turns the validated `connection_model` instance into a SQLAlchemy URL. Build it
with `sqlalchemy.URL.create`, so a password holding `@ : / # %` is escaped.

#### `get_dialect() -> str`

Returns the **sqlglot** dialect name the engine renders SQL in: `postgres`,
`tsql`, `mysql`, `duckdb`, `sqlite`, or any other name
`sqlglot.Dialect.get_or_raise` accepts. It is not the SQLAlchemy dialect name
(`postgresql`, `mssql`), which sqlglot rejects.
`tests/architecture/test_boundaries.py` checks every adapter registered under
the `nl2sql.adapters` entry point.

The class attributes `title`, `connection_model`, `driver_modules` and
`install_hint` are required too; the base sets `interface_version = 2`. See
[Class attributes](sdk.md#class-attributes).

### Optional

#### `system_schemas() -> set[str]`

The database's own schemas, never indexed whatever the scope says, matched
ignoring case. The default is none. The built-ins: PostgreSQL `pg_catalog`
and `information_schema`; MySQL `mysql`, `information_schema`,
`performance_schema` and `sys`; SQL Server `sys` and `INFORMATION_SCHEMA`;
DuckDB its `system` and `temp` catalogs.

#### `row_estimates(conn, schema) -> dict[str, int] | None`

The catalog's row count of each table in `schema`, read from the database's
statistics and never by counting. The default is `None`: the database keeps
none. A table left out of the dictionary has no estimate.

| Adapter | Source |
|---|---|
| PostgreSQL | `pg_class.reltuples` (a table never analyzed has none) |
| MySQL | `information_schema.tables.table_rows` |
| SQL Server | `sys.partitions` |
| DuckDB | `duckdb_tables().estimated_size` |
| SQLite | none: `COUNT(*)` under `profile: stats` |

#### `column_profile(sa_type) -> ColumnProfile`

What a column of the reflected SQLAlchemy type gets under `profile: stats`:
`ColumnProfile(min_max, distinct, values)`. Its null share is always read, and
`values` is only a permission: the scope's `sample_values` must be on too. The
default, `nl2sql_sdk.sqlalchemy.column_profile`:

| Type | `min_max` | `distinct` | `values` |
|---|---|---|---|
| `Boolean`, `Uuid` and the dialect types built on them (PostgreSQL `uuid`, SQL Server `bit`, `uniqueidentifier`) | no | yes | no |
| `JSON` (and `jsonb`), `LargeBinary` (`bytea`, `blob`), `ARRAY`, and a type whose name says json, blob, binary, bytea, xml or array | no | no | no |
| A type whose name says char, text, string or clob | yes | yes | yes |
| Anything else | yes | yes | no |

`MIN`/`MAX` on boolean and uuid is what PostgreSQL and SQL Server reject. A
type the driver reflects without a SQLAlchemy class is profiled in full, and a
query the database rejects for it is an issue. Dates, times, decimals and
UUIDs a driver returns for `MIN`/`MAX` are stored as ISO strings, floats and
text (`as_scalar`).

#### `render_sql(expression) -> str`

Renders the engine's finished sqlglot tree as this database's SQL. The default
is sqlglot's own rendering for `get_dialect()`. Override it to rewrite what
sqlglot cannot express for the database (SQLite rewrites date parts with
`STRFTIME`).

#### `build_engine(**options) -> Engine`

Creates the SQLAlchemy engine from `url(connection)` with the connection's
`pool`, on first use, and runs `session_setup` on every new pooled connection.
Override it to pass driver settings, handing them on as
`super().build_engine(connect_args=...)`.

#### `session_setup(dbapi_connection)`

The one place for session settings: it runs on every new pooled connection,
before its first query, with the driver's own connection. The default does
nothing. Make the session read-only and set the statement timeout here, and
declare `ENFORCES_READ_ONLY` and `ENFORCES_TIMEOUT` in `capabilities()` only
for what it really enforces.

The base keeps `limits.timeout_ms` but does not enforce it: SQLAlchemy has no
generic statement timeout and silently ignores an `execution_options["timeout"]`.
The built-ins:

| Adapter | Read-only | Timeout |
| :--- | :--- | :--- |
| PostgreSQL | `SET default_transaction_read_only = on` | `SET statement_timeout` |
| MySQL | `SET SESSION TRANSACTION READ ONLY` | `SET SESSION MAX_EXECUTION_TIME` |
| SQL Server | none (it has no such setting) | pyodbc's query timeout |
| DuckDB | `read_only=True` (a connect argument, in `build_engine`) | `interrupt()` on a timer, in `build_engine` |
| SQLite | `mode=ro` in the URL | a progress handler per statement, in `build_engine` |

A setting that is per statement rather than per session (DuckDB's timer,
SQLite's deadline) goes in `build_engine` as a SQLAlchemy event. An adapter
that does neither has no statement timeout.

#### `classify(exc) -> str | None`

The stage a failed `test_connection` failed at, or `None` when the error does
not say. The default reads a missing driver module (`driver`) and the standard
SQLSTATE: class `08` is `network`, `28` `auth`, `3D` `database`, and `42501`
`permission`. Override it to add your driver's own codes and call `super()` for
the rest; the built-ins add MySQL's error numbers, SQL Server's, and
PostgreSQL's connection messages, which carry no SQLSTATE.

`test_connection` connects, runs `SELECT 1` and lists the schemas. A failure
to connect gets `classify`'s stage; a connection that cannot list the schemas
is `permission`. Each stage has a fix: the `install_hint` for `driver`, "check
the host, port and firewall" for `network`, and so on.

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

#### `local_path(path) -> str`

For a file-based database. The engine passes each adapter `folder`, the
folder of the `nl2sql.yaml` the datasource came from (None when the config was
built in code). `local_path` reads a relative path from there, and leaves an
absolute path and `:memory:` as they are. The SQLite and DuckDB adapters call it
in `url`.

### Also provided

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

#### `close()`

Disposes the connection pool, if one was made. The engine calls an adapter's
`close()` once, from `engine.close()`.
