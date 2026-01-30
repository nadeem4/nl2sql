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

class MyPostgresAdapter(BaseSQLAlchemyAdapter):
    def construct_uri(self, args):
        return f"postgresql://{args['user']}:{args['password']}@{args['host']}/{args['database']}"
```

## 🔌 Adapter Interface Explained

Each method in the adapter plays a specific role in the **Plan-Validate-Execute** loop of the agent.

### 1. Core Execution

| Method | Purpose | Who Calls It? |
| :--- | :--- | :--- |
| `connect()` | Establishes the connection pool to the database using SQLAlchemy. | Initialization |
| `execute(request)` | Runs the request and returns a standardized `ResultFrame`. | **Executor Node** |
| `fetch_schema_snapshot()` | Introspects the database to retrieve Tables, Columns (with Types), Foreign Keys, and usage statistics. | **Indexing Service** |

### 2. Planning & Safety Capabilities

These methods turn a "dumb" runner into a "smart" agent.

| Method | Description | Usage |
| :--- | :--- | :--- |
| `cost_estimate(sql)` | Returns `CostEstimate` (rows, cost, time). | **Safeguards** use this to block expensive queries (e.g. > 1M rows). |
| `dry_run(sql)` | Returns `DryRunResult` (valid, error). | **Validator Node** uses this to check if SQL is executable before running it. |
| `explain(sql)` | Returns the query execution plan (JSON/XML). | **Optimizer Agent** can use this to detect full table scans or inefficient joins. |
