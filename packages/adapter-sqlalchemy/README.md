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
