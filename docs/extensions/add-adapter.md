# Adding a New Adapter

This guide describes how to add a new datasource adapter and make it available to NL2SQL.

## 1. Implement the adapter contract

All adapters implement `DatasourceAdapter`, version 2, and declare a `connection_model`. See `../adapters/sdk.md` for the authoritative contract and required fields/methods.

If you are building a SQL adapter, extend `SQLAlchemyAdapter` from `nl2sql_sdk.sqlalchemy` (`pip install "nl2sql-sdk[sqlalchemy]"`): it connects, executes, reads the schema the datasource's `index` scope wants with catalog row estimates and type-aware statistics, and reports what it could not read as issues. You implement `url` and `get_dialect`, and override `system_schemas`, `row_estimates` or `column_profile` where your database differs. See [SQLAlchemy adapters](../adapters/sqlalchemy.md) and the complete example in [Building adapters](../adapters/development.md).

## 2. Register it

The engine finds an adapter by its `type` name in the
[registry](../architecture/configuration_v2.md#5-the-registry). There are two
ways to put a name there.

**From a package**, declare an entry point in the `nl2sql.adapters` group of
your `pyproject.toml`:

```toml
[project.entry-points."nl2sql.adapters"]
snowflake = "nl2sql_snowflake.adapter:SnowflakeAdapter"
```

`Registry.default()` lists every installed entry point by name and imports none
of them. An adapter is imported when a datasource uses its `type`.

**In your own code**, register the class on a registry you build:

```python
from nl2sql.registry import Registry

registry = Registry.default()

@registry.adapter("snowflake")
class SnowflakeAdapter: ...

# or: registry.add_adapter("snowflake", SnowflakeAdapter)
```

A registration belongs to that registry only, so two engines or two tests never
see each other's. Adding a name that is already there, including a built-in,
raises unless you pass `replace=True`.

When the adapter is loaded, the registry checks three things and stops with a
message that says how to fix each:

| Check | Fails with |
|---|---|
| `interface_version` is the version this engine needs (2) | `nl2sql-snowflake implements adapter interface 1; this engine needs 2.` |
| every module in `driver_modules` is installed (`importlib.util.find_spec`, without importing it) | the missing modules and your `install_hint` |
| only one installed package declares the name | both package names |

An unknown `type` suggests the closest registered name. `nl2sql list-adapters`
shows every registered adapter and whether it can be loaded.

## 3. Configure the datasource

Add a datasource to the `datasources` section of `nl2sql.yaml`, for example as
its own file in `datasources/`:

```yaml
# datasources/sales_db.yaml
type: postgres
description: "Sales warehouse"
connection:
  host: "${env:PG_HOST}"
  database: sales
  user: "${env:PG_USER}"
  password: "${env:PG_PASSWORD}"
```

References under `connection` are resolved by the engine's `SecretManager`
when the adapter is built, and the block is then validated against the
adapter's `connection_model`: an unknown key is an error.

## 4. Validate capabilities

Ensure `capabilities()` advertises the correct set for routing and execution:

- `supports_sql` if SQL execution is supported.
- `supports_bound_parameters` if `execute` (and `execute_batches`) bind
  `AdapterRequest.parameters` by name to `:name` placeholders through the
  driver. Without it, a verified query with parameters is never sent to the
  adapter. Adapters built on `SQLAlchemyAdapter` inherit both the binding
  and the capability.
- `enforces_read_only` and `enforces_timeout` only when the session cannot
  write, and a statement past `limits.timeout_ms` is stopped, on a real server.

## 5. Optional: let `scan()` find it

`engine.datasources.scan()` offers the databases it finds in a connection URL,
in files under a folder and on the default ports of `127.0.0.1`. It finds an
adapter only if the adapter declares how, with the four members of
[`DiscoverableAdapter`](../adapters/sdk.md#optional-discoverableadapter):

```python
from nl2sql_sdk.discovery import Found

class SnowflakeAdapter:
    file_suffixes = ()
    default_ports = ()
    url_schemes = ("snowflake",)

    @classmethod
    def connection_for(cls, found: Found) -> dict | None:
        if found.kind != "url":
            return None
        ...  # parse found.value into the fields of SnowflakeConnection; never connect
```

An adapter without them still works; it is just chosen by name instead of found.

## Source references

- Adapter protocol: `packages/sdk/src/nl2sql_sdk/protocols.py`
- Registry: `packages/nl2sql/src/nl2sql/registry.py`
- Datasource registry: `packages/nl2sql/src/nl2sql/datasources/registry.py`
- SQLAlchemy base adapter: `packages/sdk/src/nl2sql_sdk/sqlalchemy/adapter.py`
