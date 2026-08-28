# DuckDB Adapter

In-process analytical adapter for local files and ad-hoc analysis.

!!! info "Implementation"
    This adapter extends `BaseSQLAlchemyAdapter` without overriding `connect()`: DuckDB
    runs in-process and exposes no server-side statement timeout to configure.

## Installation

The SQLAlchemy dialect lives in `duckdb-engine`, which ships in its own extra:

```bash
pip install "nl2sql[duckdb]"
```

## Configuration

**Type**: `duckdb`

```yaml

connection:
  type: "duckdb"
  database: "./warehouse.duckdb" # File path, or ":memory:"
```

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
| **Timeout** | Not supported | DuckDB is in-process and has no statement timeout. |
| **Dry Run** | `EXPLAIN` | Plans the query without executing it. |
| **Explain** | `EXPLAIN` | Returns DuckDB's rendered operator tree as text. |
| **Costing** | Stubbed | Returns a fixed cost of `1.0`; see below. |

### Optimization Details

* **Dry Run**: Uses `EXPLAIN {sql}`. DuckDB binds and plans the statement without running
  it, so success means the SQL parses and every referenced table and column resolves.
* **Explain**: Returns the `physical_plan` text of `EXPLAIN {sql}` — DuckDB's box-drawn
  operator tree. It is human-readable, not machine-parsable.
* **Cost Estimate**: **Stubbed.** DuckDB's `EXPLAIN` prints an operator tree carrying no
  cost or cardinality figures, so there is nothing real to report. The adapter validates
  the query and then returns a fixed `estimated_cost=1.0` / `estimated_rows=10`, exactly
  as the SQLite adapter does. Do not use these numbers for planning or budgeting; a
  failed validation is signalled by `estimated_cost=-1.0`.

### Schema Introspection

DuckDB qualifies schema names with their catalog (`memory.main`, `warehouse.main`). The
adapter excludes `system.main`, `system.information_schema` and `temp.main`, leaving the
schemas of the attached database.

## Hints

* **Analytics**: DuckDB is columnar and vectorized, which suits aggregation-heavy
  generated SQL. It reads Parquet and CSV directly via `read_parquet()` / `read_csv()`.
* **Concurrency**: A DuckDB file allows a single writing process. Treat it as a
  single-writer analytical store, not a shared OLTP database.
