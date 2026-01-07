# Adapter Development Guide

The NL2SQL Platform uses a modular Adapter architecture to support any database engine.

## 1. Overview

Adapters bridge the gap between our semantic engine and specific database drivers. They implement the `DatasourceAdapter` protocol.

## 2. Creating a New Adapter

### Step 1: Subclass Base Adapter

Inherit from `BaseSQLAlchemyAdapter` to get free connection handling, introspection, and dry-run support.

```python
from nl2sql_sqlalchemy_adapter import BaseSQLAlchemyAdapter

class MyDbAdapter(BaseSQLAlchemyAdapter):
    
    def connect(self) -> None:
        """Override to handle specific timeout or connection logic."""
        super().connect() 
```

### Step 2: Implement Protocol Methods

You must ensure these methods work for your dialect:

* `fetch_schema()`: (Handled by Base via SQLAlchemy Inspector)
* `estimate_cost(sql)`: Return query cost/rows.
* `dry_run(sql)`: Verify SQL without committing.
* `explain(sql)`: Return execution plan.

### Step 3: Deployment

1. Package your adapter (e.g. `nl2sql-adapter-mydb`).
2. Register it via Python Entry Points in `pyproject.toml`:

```toml
[project.entry-points."nl2sql.adapters"]
mydb = "nl2sql_mydb.adapter:MyDbAdapter"
```

## 3. Handling Timeouts

Your adapter receives `statement_timeout_ms` (int, milliseconds) in `__init__`.

* **Standard approach**: Use `BaseSQLAlchemyAdapter` logic (maps to `execution_options={"timeout": s}`).
* **Native approach**: Override `connect()` and inject dialect-specific arguments (e.g., `connect_args={"options": "-c statement_timeout=..."}` for Postgres).

## 4. Testing

Use the `packages/adapter-sdk/tests/conftest.py` fixtures to verify your adapter against a real database instance.
