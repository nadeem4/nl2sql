# Building Adapters

The **Adapter SDK** (`nl2sql-adapter-sdk`) allows you to extend the platform to support new databases or APIs.

## Implementing an Adapter

You must implement the `DatasourceAdapter` interface.

### Mandatory Properties

* `datasource_id`: Unique identifier (e.g. "postgres_prod").
* `row_limit`: **Safety Breaker**. Must return `1000` (or config value) to prevent massive result sets.
* `max_bytes`: **Safety Breaker**. limit result size at the network/driver level if possible.

### Mandatory Methods

* `fetch_schema()`: Must return `SchemaMetadata` with `tables`, `columns`, `pks`, `fks`. *Crucially, it should also populate `col.statistics` (samples, min/max) for Indexing.*
* `execute(sql)`: Returns `QueryResult`.
* `dry_run(sql)`: Returns validity checks.

### Optional Optimization

* `explain(sql)`: Returns query plan.
* `cost_estimate(sql)`: Returns estimated rows/time. used by PhysicalValidator.

::: nl2sql_adapter_sdk.interfaces.DatasourceAdapter

## Compliance Testing

The SDK provides a compliance test suite. **All Adapters MUST pass this suite.**

It verifies:

* Schema Introspection (PKs/FKs detected?)
* Type Mapping (Date -> Python Date, Numeric -> Python Float)
* Error Handling (Bad SQL -> AdapterError)

```python
# tests/test_my_adapter.py
from nl2sql_adapter_sdk.testing import BaseAdapterTest
from my_adapter import MyAdapter

class TestMyAdapter(BaseAdapterTest):
    @pytest.fixture
    def adapter(self):
        return MyAdapter(...)
```

## Choosing a Base Class

The platform provides two ways to build adapters. Choose the one that fits your target datasource.

| Feature | `DatasourceAdapter` (Base Interface) | `BaseSQLAlchemyAdapter` (Helper Class) |
| :--- | :--- | :--- |
| **Package** | `nl2sql-adapter-sdk` | `nl2sql-adapter-sqlalchemy` |
| **Best For** | REST APIs, NoSQL, GraphQL, Manual SQL Drivers. | SQL Databases with SQLAlchemy dialects (Postgres, Oracle, Snowflake). |
| **Schema Fetching** | **Manual Implementation Required**. You must map metadata to `SchemaMetadata`. | **Automatic**. Uses `sqlalchemy.inspect` to reflect tables/FKs. |
| **Execution** | **Manual Implementation Required**. You handle connections, cursors, and types. | **Automatic**. Handles pooling, transactions, and result formatting. |
| **Stats Gathering** | **Manual**. You write queries to fetch min/max/nulls. | **Automatic**. Runs optimized generic queries for stats. |
| **Dry Run** | **Manual**. | **Automatic**. Uses transaction rollback pattern. |

### When to use `DatasourceAdapter`?

Use the raw interface when:

1. You are connecting to a non-SQL source (e.g., Elasticsearch, HubSpot API).
2. You are using a customized internal SQL driver that is not compatible with SQLAlchemy.
3. You need complete control over the execution lifecycle (e.g. async-only drivers).

### When to use `BaseSQLAlchemyAdapter`?

Use this helper class when:

1. There is an existing SQLAlchemy dialect for your database (this covers 95% of SQL databases).
2. You want to save time on boilerplate (connection pooling, schema reflection).
3. You want consistent behavior with the core supported adapters.

## Building SQL Adapters (The Fast Way)

For SQL databases supported by SQLAlchemy, you should use the `nl2sql-adapter-sqlalchemy` package as described in the comparison above.

### `BaseSQLAlchemyAdapter` Features

This base class implements ~90% of the required functionality for you:

* **Automatic Schema Fetching**: Uses `sqlalchemy.inspect` to get tables, columns, PKs.
* **Automatic Statistics**: Runs optimized queries to fetch `min/max`, `null_percentage`, `distinct_count`, and `sample_values` for text columns.
* **Generic Execution**: Handles connection pooling and result formatting.
* **Safety**: Built-in generic `dry_run` using transaction rollbacks.

### Example Implementation

See `packages/adapters/postgres` for a reference implementation.

```python
from nl2sql_sqlalchemy_adapter import BaseSQLAlchemyAdapter

class PostgresAdapter(BaseSQLAlchemyAdapter):
    def construct_uri(self, args: Dict[str, Any]) -> str:
        return f"postgresql://{args.get('user')}:{args.get('password')}@{args.get('host')}/{args.get('database')}"
    
    # Optional: Override dry_run for better performance using EXPLAIN
    def dry_run(self, sql: str):
        self.execute(f"EXPLAIN {sql}")
        return DryRunResult(is_valid=True)
```

## Reference Adapters

For detailed usage configurations of our supported adapters, please see the **[Supported Adapters](index.md)** section.

Explore the `packages/adapters/` directory for examples:

* `postgres`: Standard implementation using `sqlalchemy`.
* `sqlite`: Simple, file-based.
* `mssql` / `mysql`: Standard enterprise drivers.

## Next Steps

Check out the [Postgres Adapter Source Code](https://github.com/nadeem4/nl2sql/tree/main/packages/adapters/postgres) for a complete, production-grade example.
