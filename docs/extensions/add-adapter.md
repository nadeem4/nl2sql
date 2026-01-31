# Adding a New Adapter

This guide describes how to add a new datasource adapter and make it available to NL2SQL.

## 1. Implement the adapter contract

All adapters must implement `DatasourceAdapterProtocol`. See `../adapters/sdk.md` for the authoritative contract and required fields/methods.

If you are building a SQL adapter, extending `BaseSQLAlchemyAdapter` can provide schema introspection and default SQL execution. See `../adapters/sqlalchemy.md`.

## 2. Expose an entry point

Adapters are discovered via `nl2sql.adapters` entry points. Ensure your package exposes:

```
nl2sql.adapters = 
  postgres = my_pkg.adapters.PostgresAdapter
```

## 3. Configure the datasource

Add a datasource entry in `configs/datasources.yaml`:

```yaml
datasources:
  - id: sales_db
    description: "Sales warehouse"
    connection:
      type: postgres
      host: ${env:PG_HOST}
      user: ${env:PG_USER}
      password: ${env:PG_PASSWORD}
```

Secrets are resolved by `SecretManager` before the registry initializes.

## 4. Validate capabilities

Ensure `capabilities()` advertises the correct set for routing and execution:

- `supports_sql` if SQL execution is supported.
- `supports_schema_introspection` if schema can be fetched.
- `supports_dry_run` or `supports_cost_estimate` if available.

## Source references

- Adapter protocol: `packages/adapter-sdk/src/nl2sql_adapter_sdk/protocols.py`
- Adapter discovery: `packages/core/src/nl2sql/datasources/discovery.py`
- Datasource registry: `packages/core/src/nl2sql/datasources/registry.py`
- SQLAlchemy base adapter: `packages/adapter-sqlalchemy/src/nl2sql_sqlalchemy_adapter/adapter.py`
