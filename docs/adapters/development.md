# Building Adapters Guide

The NL2SQL Platform is designed to be extensible. You can build adapters for any datasource, from SQL databases to REST APIs.

## Implementation Path

There are two primary ways to build an adapter. Choose the one that fits your target:

| If you are checking... | Use... | Reference |
| :--- | :--- | :--- |
| A standard SQL Database (Postgres, Oracle, Snowflake) | `nl2sql-adapter-sqlalchemy` | **[SQLAlchemy Adapter Reference](sqlalchemy.md)** |
| A NoSQL DB, REST API, or custom driver | `nl2sql-adapter-sdk` | **[Adapter SDK Reference](sdk.md)** |

## Option 1: The "Fast Lane" (SQLAlchemy)

For 95% of use cases, you are connecting to a SQL database that already has a Python SQLAlchemy dialect.

**Use `BaseSQLAlchemyAdapter`**. It handles:

* Automatic Schema Introspection (Tables, PKs, FKs)
* Connection Pooling
* Statistic Gathering
* Transaction-based Dry Runs

### Example

```python
from nl2sql_sqlalchemy_adapter import BaseSQLAlchemyAdapter

class PostgresAdapter(BaseSQLAlchemyAdapter):
    def construct_uri(self, args: Dict[str, Any]) -> str:
        # Convert args to connection string
        return f"postgresql://{args['user']}:{args['password']}@{args['host']}/{args['database']}"
```

> See the **[SQLAlchemy Adapter Reference](sqlalchemy.md)** for full API details.

## Option 2: The "Custom" Path (SDK)

If you need to connect to something else (e.g., ElasticSearch, a CRM API, or a raw SQL driver), you must implement the raw interface.

**Implement `DatasourceAdapter`**. You must manually handle:

* Fetching and normalizing schema metadata.
* Executing queries and formatting results.
* Implementing safety breakers (`row_limit`).

### Example

```python
from nl2sql_adapter_sdk import DatasourceAdapter

class MyRestAdapter(DatasourceAdapter):
    def fetch_schema(self) -> SchemaMetadata:
        # call API, return schema
        pass

    def execute(self, query: str) -> QueryResult:
        # run query, return rows
        pass
```

> See the **[Adapter SDK Reference](sdk.md)** for the mandatory method signatures and compliance testing guide.

## Compliance Testing

Regardless of which path you choose, your adapter **MUST** pass the compliance test suite to ensuring it handles types and errors correctly.

```python
from nl2sql_adapter_sdk.testing import BaseAdapterTest
# ... see SDK Reference for test setup
```
