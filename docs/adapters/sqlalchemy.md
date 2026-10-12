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
| **Automatic Stats** | Runs optimized generic SQL queries to fetch `min`, `max`, `null_percentage`, and `distinct_count`. |
| **Connection Pooling** | Manages engine lifecycle and connection pools. |
| **Transaction Safety** | Implements generic `dry_run` using transaction rollbacks. |

### Required Overrides

#### `construct_uri(args: Dict[str, Any]) -> str`

Converts a configuration dictionary into a SQLAlchemy connection string.

* **Args**: `args` - The `connection` dictionary from `datasources.yaml`.
* **Returns**: A valid URL (e.g., `postgresql://...`).

### Optional Overrides

#### `connect()`

Override to provide custom connection arguments (e.g., timeouts, isolation levels).

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

#### `is_retryable_error(exc) -> bool`

`execute()` catches every `SQLAlchemyError` the query raises and returns it as
a failed `ResultFrame` (`query_failed(exc)`), with `retryable` from this method
(see [the adapter SDK](sdk.md)). The default reads the driver's SQLSTATE when
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
