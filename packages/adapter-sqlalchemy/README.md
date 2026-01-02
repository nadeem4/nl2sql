# NL2SQL SQLAlchemy Adapter

The `nl2sql-adapter-sqlalchemy` package provides a base implementation for SQL-based adapters using [SQLAlchemy](https://www.sqlalchemy.org/).

It is designed to reduce code duplication by implementing the common logic for:

* **Connection Management**: `create_engine` and `validate_connection`.
* **Schema Introspection**: `fetch_schema` (tables, columns, foreign keys, comments).
* **Query Execution**: `execute` with standard result formatting.

## 📦 Installation

```bash
pip install -e packages/adapter-sqlalchemy
```

## 🔨 Usage

Inherit from `BaseSQLAlchemyAdapter` when creating a new SQL adapter:

```python
from nl2sql_sqlalchemy_adapter import BaseSQLAlchemyAdapter
from nl2sql_adapter_sdk import CapabilitySet

class MyPostgresAdapter(BaseSQLAlchemyAdapter):
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            supports_cte=True,
            supports_window_functions=True
        )
```

## 🔌 Adapter Interface Explained

Each method in the `DatasourceAdapter` plays a specific role in the **Plan-Validate-Execute** loop of the agent.

### 1. Core Execution

| Method | Purpose | Who Calls It? |
| :--- | :--- | :--- |
| `connect()` | Establishes the connection pool to the database using SQLAlchemy. | Initialization |
| `execute(sql)` | Runs the actual query and returns a standardized `QueryResult`. | **Executor Node** |
| `fetch_schema()` | Introspects the database to retrieve Tables, Columns (with Types), Foreign Keys, and usage statistics. | **Indexing Service** |

### 2. Planning & Safety Capabilities

These methods turn a "dumb" runner into a "smart" agent.

| Method | Purpose | Why it matters? |
| :--- | :--- | :--- |
| `capabilities()` | Returns a set of supported features (CTE, Window Functions, Returning, etc.). | **Planner Node** uses this to avoid generating SQL syntax that the specific DB doesn't support. |
| `dry_run(sql)` | Checks if the SQL is valid **without running it** (usually via `EXPLAIN` or Transaction Rollback). | **Validator Node** calls this to verify the query against the *real* schema/permissions before attempting execution. |
| `explain(sql)` | Returns the query execution plan (JSON/XML). | **Optimizer Agent** can use this to detect full table scans or inefficient joins. |
| `cost_estimate(sql)` | Returns predicted resource usage (`cpu_cost`, `estimated_rows`). | **Safety Layer** uses this to block expensive queries (e.g. > 1M rows) before they crash the DB. |
