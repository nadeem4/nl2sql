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
from pydantic import Field, SecretStr
from sqlalchemy import URL

from nl2sql_sdk import ConnectionModel
from nl2sql.adapters.sqlalchemy_base import BaseSQLAlchemyAdapter


class PostgresConnection(ConnectionModel):
    host: str = Field(title="Host")
    port: int = Field(5432, title="Port")
    database: str = Field(title="Database")
    user: str = Field(title="User")
    password: SecretStr = Field(title="Password")


class PostgresAdapter(BaseSQLAlchemyAdapter):
    title = "PostgreSQL"
    connection_model = PostgresConnection
    driver_modules = ("psycopg2",)
    install_hint = 'pip install "nl2sql-engine[postgres]"'

    def url(self, connection: PostgresConnection) -> URL:
        # URL.create escapes the credentials; an f-string breaks on a password
        # containing @ : / # or %.
        return URL.create(
            "postgresql+psycopg2",
            username=connection.user,
            password=connection.password.get_secret_value(),
            host=connection.host,
            port=connection.port,
            database=connection.database,
        )
```

Enforce `limits.timeout_ms` in `build_engine()` too: the base class keeps it but
does not apply it (see the [reference](sqlalchemy.md#build_engine-engine)).

> See the **[SQLAlchemy Adapter Reference](sqlalchemy.md)** for full API details.

## Option 2: The "Custom" Path (Protocol)

If you need to connect to something else (e.g., ElasticSearch, a CRM API, or a raw SQL driver), implement the core adapter protocol.

**Implement `DatasourceAdapter`**. You must manually handle:

* Fetching and normalizing schema metadata.
* Executing queries and formatting results.
* Declaring a `connection_model`: a `ConnectionModel` subclass with one field per connection setting. The engine validates the datasource's `connection` against it (unknown keys are errors) and passes the instance to your constructor.
* The constructor the engine builds every adapter with: `adapter_cls(datasource_id, connection, limits, *, folder=None)`, where `limits` is the datasource's `Limits` and `folder` the folder of `nl2sql.yaml`, or None. Do not connect in the constructor: `engine.check()` calls `test_connection()`, which returns a `ConnectionCheck`.
* A `close()` that releases connections; `engine.close()` calls it once.
* Declaring `interface_version = 2`, a `title`, the `driver_modules` it imports to connect, and an `install_hint` (see [Class attributes](sdk.md#class-attributes)).
* A `default_schema()` naming the schema an unqualified table resolves to, or returning None. Policies read a table with no schema as a table in this schema; without one, `main` is assumed.
* A `fetch_schema_snapshot(scope, on_table=None)` that reads only what the datasource's `IndexScope` wants (`scope.wants_table`, `scope.wants_column`).

### Example

```python
from pydantic import SecretStr

from nl2sql_sdk import (AdapterRequest, ConnectionCheck, ConnectionModel, IndexScope, Limits,
                        ResultFrame, SchemaSnapshot)


class MyApiConnection(ConnectionModel):
    url: str
    token: SecretStr


class MyApiAdapter:
    interface_version = 2
    title = "My API"
    connection_model = MyApiConnection
    driver_modules = ("httpx",)
    install_hint = 'pip install "nl2sql-my-api"'

    def __init__(self, datasource_id: str, connection: MyApiConnection, limits: Limits, *, folder=None):
        self.datasource_id, self.connection, self.limits = datasource_id, connection, limits

    def capabilities(self):
        return set()  # not SQL: the SQL agent does not serve it

    def test_connection(self) -> ConnectionCheck:
        ...  # call the API; return ConnectionCheck(ok=False, message=...) on failure

    def fetch_schema_snapshot(self, scope: IndexScope, on_table=None) -> SchemaSnapshot:
        ...  # call the API, return the schema of what scope.wants_table() keeps

    def execute(self, request: AdapterRequest) -> ResultFrame:
        ...  # run the request, return rows

    def get_dialect(self) -> str: ...
    def default_schema(self): return None
    def close(self): pass
```

> See the **[Adapter Interface Reference](sdk.md)** for the method signatures and compliance guide.

## Compliance Testing

Regardless of which path you choose, your adapter **MUST** pass the compliance test suite to ensure it handles types and errors correctly.

```python
# See Adapter Interface Reference for test setup
```
