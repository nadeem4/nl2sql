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
