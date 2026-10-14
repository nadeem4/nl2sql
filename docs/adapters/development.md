# Building Adapters Guide

The NL2SQL Platform is designed to be extensible. You can build adapters for any datasource, from SQL databases to REST APIs.

## Implementation Path

There are two primary ways to build an adapter. Choose the one that fits your target:

| If you are checking... | Use... | Reference |
| :--- | :--- | :--- |
| A standard SQL Database (Postgres, Oracle, Snowflake) | `nl2sql.adapters.sqlalchemy_base` | **[SQLAlchemy Adapter Reference](sqlalchemy.md)** |
| A NoSQL DB, REST API, or custom driver | Core adapter protocol | **[Adapter Interface Reference](sdk.md)** |

## Option 1: The "Fast Lane" (SQLAlchemy)

For 95% of use cases, you are connecting to a SQL database that already has a Python SQLAlchemy dialect.

**Use `BaseSQLAlchemyAdapter`**. It handles:

* Automatic Schema Introspection (Tables, PKs, FKs)
* Connection Pooling
* Statistic Gathering
* Transaction-based Dry Runs

### Example

```python
from sqlalchemy import URL
from nl2sql.adapters.sqlalchemy_base import BaseSQLAlchemyAdapter

class PostgresAdapter(BaseSQLAlchemyAdapter):
    interface_version = 1
    driver_modules = ("psycopg2",)
    install_hint = 'pip install "nl2sql-engine[postgres]"'

    def construct_uri(self, args: Dict[str, Any]) -> str:
        # URL.create escapes the credentials; an f-string breaks on a password
        # containing @ : / # or %.
        return URL.create(
            "postgresql+psycopg2",
            username=args["user"],
            password=args["password"],
            host=args["host"],
            database=args["database"],
        ).render_as_string(hide_password=False)
```

Enforce `statement_timeout_ms` in `connect()` too: the base class records it but
does not apply it (see the [reference](sqlalchemy.md#connect)).

> See the **[SQLAlchemy Adapter Reference](sqlalchemy.md)** for full API details.

## Option 2: The "Custom" Path (Protocol)

If you need to connect to something else (e.g., ElasticSearch, a CRM API, or a raw SQL driver), implement the core adapter protocol.

**Implement `DatasourceAdapterProtocol`**. You must manually handle:

* Fetching and normalizing schema metadata.
* Executing queries and formatting results.
* Implementing safety breakers (`row_limit`).
* Declaring `interface_version = 1`, the `driver_modules` it imports to connect, and an `install_hint` (see [Class attributes](sdk.md#class-attributes)).
* Optionally, a `default_schema` attribute naming the schema an unqualified table resolves to. RBAC reads a two-part `datasource.table` policy entry as that table in this schema; without it, `main` is assumed.

### Example

```python
from nl2sql_sdk.protocols import DatasourceAdapterProtocol
from nl2sql_sdk.contracts import AdapterRequest, ResultFrame
from nl2sql_sdk.capabilities import DatasourceCapability

class MyRestAdapter(DatasourceAdapterProtocol):
    interface_version = 1
    driver_modules = ("httpx",)
    install_hint = 'pip install "nl2sql-my-rest"'

    def capabilities(self):
        return {DatasourceCapability.SUPPORTS_REST}

    def fetch_schema_snapshot(self):
        # call API, return schema
        pass

    def execute(self, request: AdapterRequest) -> ResultFrame:
        # run request, return rows
        pass
```

> See the **[Adapter Interface Reference](sdk.md)** for the method signatures and compliance guide.

## Compliance Testing

Regardless of which path you choose, your adapter **MUST** pass the compliance test suite to ensure it handles types and errors correctly.

```python
# See Adapter Interface Reference for test setup
```
