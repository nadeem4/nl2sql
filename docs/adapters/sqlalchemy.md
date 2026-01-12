# SQLAlchemy Adapter Reference

The **SQLAlchemy Adapter** (`nl2sql-adapter-sqlalchemy`) provides a helper base class for building adapters for any SQL database supported by SQLAlchemy.

## Base Class: `BaseSQLAlchemyAdapter`

Constructs a robust adapter by wrapping standard SQLAlchemy components.

```python
from nl2sql_sqlalchemy_adapter import BaseSQLAlchemyAdapter
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

Returns the logical dialect name. Defaults to the engine driver name.

#### `explain(sql: str)` / `cost_estimate(sql: str)`

The base class provides stubs. Override these to implement database-specific optimization logic (e.g., `EXPLAIN ANALYZE`).
