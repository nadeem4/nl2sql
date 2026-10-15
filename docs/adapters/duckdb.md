# DuckDB Adapter

In-process analytical adapter for local files and ad-hoc analysis.

!!! info "Implementation"
    This adapter extends `SQLAlchemyAdapter`. It overrides `build_engine()` to
    open a file with `read_only=True` and to enforce `limits.timeout_ms`: DuckDB
    runs in-process and has no statement timeout setting, so the adapter
    interrupts a statement that runs too long. It overrides `schema_names()`
    and `reflect()` to read DuckDB's own catalog (see below).

## Installation

The SQLAlchemy dialect lives in `duckdb-engine`, which ships in its own extra:

```bash
pip install "nl2sql-engine[duckdb]"
```

The adapter reads a schema from DuckDB's own catalog (`duckdb_schemas()`,
`duckdb_tables()`, `duckdb_views()`, `duckdb_columns()` and
`duckdb_constraints()`) rather than through `duckdb-engine`'s reflection,
which reuses SQLAlchemy's PostgreSQL reflection and queries `pg_catalog`
tables DuckDB does not have. A column's type is DuckDB's own name:
`DECIMAL(10,2)`, `VARCHAR[]`, `JSON`.

## Configuration

**Type**: `duckdb`

```yaml
# datasources/warehouse.yaml
type: duckdb
connection:
  database: ./warehouse.duckdb # File path, or ":memory:"
```

`database` is the only key (`DuckdbConnection`); any other is an error.

!!! warning "Persistence"
    `:memory:` databases live for the lifetime of the process. Use a file path for
    anything that must survive a restart, and an absolute path under a mounted volume
    when running in **Docker**.

### Connection Details

* **Driver**: `duckdb-engine` (the `duckdb:///` SQLAlchemy dialect) over the `duckdb` package.
* **URI Constructed**: `duckdb:///{database}` — for example `duckdb:///./warehouse.duckdb`
  or `duckdb:///:memory:`.

## Features

| Feature | Implementation | Note |
| :--- | :--- | :--- |
| **Read-only** | `read_only=True` on the connection | A file is always opened read-only, so it must exist, and declares `ENFORCES_READ_ONLY`. DuckDB cannot open `:memory:` read-only, and an in-memory database has nothing to protect, so it does not declare it. |
| **Timeout** | `interrupt()` on a timer | See [Statement Timeout](#statement-timeout). Declares `ENFORCES_TIMEOUT`. |
| **Connection stages** | The message | A file that does not exist is `database`. |
| **Estimates** | `EXPLAIN (FORMAT JSON)`, then `duckdb_tables().estimated_size` | `estimate` never runs the query. A scan of a table counts the table's size, not the operator's estimate; a table function (`range`, `read_parquet`) counts its own estimate. DuckDB reports no cost. Declares `ESTIMATES_COST`. |
| **Row counts** | `duckdb_tables().estimated_size` | The catalog's count. |
| **Stats** | One query per column | See [Reading a schema](sqlalchemy.md#reading-a-schema). |

### Statement Timeout

When `limits.timeout_ms` is set, each statement starts a timer that calls the
DuckDB connection's `interrupt()` once the limit passes. DuckDB stops the query and
raises `InterruptException`, which the adapter reports as a timeout
(`QUERY_TIMEOUT`) that the engine does not retry. The timer is cancelled as soon as
the statement finishes or fails, so it never touches a later query. The limit
covers running the statement; DuckDB materialises the result while it runs, so
fetching rows afterwards is not timed.

### Schema Introspection

DuckDB qualifies schema names with their catalog (`memory.main`, `warehouse.main`). The
adapter excludes `system.main`, `system.information_schema` and `temp.main`, leaving the
schemas of the attached database.

## Hints

* **Analytics**: DuckDB is columnar and vectorized, which suits aggregation-heavy
  generated SQL. It reads Parquet and CSV directly via `read_parquet()` / `read_csv()`.
* **Concurrency**: A DuckDB file allows a single writing process, and a process
  that has it open for writing keeps a read-only one out. Write to the file
  from another process only while the engine is not using it.
