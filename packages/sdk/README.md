# nl2sql-sdk

The interfaces an extension for the
[nl2sql engine](https://github.com/nadeem4/nl2sql) implements: database
adapters, secret providers, stores and policy checks. The import package is
`nl2sql_sdk`. Its core depends
on pydantic and the standard library only, so an extension built against it
inherits nothing else.

- `DatasourceAdapter`: `capabilities()`, `test_connection()`,
  `fetch_schema_snapshot(scope)`, `execute(request)` and `get_dialect()`, which
  returns a sqlglot dialect name such as `postgres` or `tsql`. An adapter class
  also declares `interface_version = 2`, a `connection_model`, the
  `driver_modules` it needs and an `install_hint` for when one is missing.
- `nl2sql_sdk.sqlalchemy` (`pip install "nl2sql-sdk[sqlalchemy]"`):
  `SQLAlchemyAdapter`, the base the built-in adapters are built on, and
  `SQLAlchemyConnection`. The one part of the SDK that imports SQLAlchemy.
- `AdapterRequest`, `ResultFrame` and `ResultError`: what the engine sends an
  adapter and what it gets back.
- `SchemaSnapshot` and the table, column and foreign-key contracts an adapter
  describes its schema with.
- `SecretProvider`: `interface_version = 1`, a strict `config_model` for its
  block in the secrets config, `from_config(cfg)` and `get_secret(key)`.
- `nl2sql_sdk.stores`: the index, state, trace and results store interfaces.
- `nl2sql_sdk.policy`: `PolicyCheck` (`interface_version = 1`, a strict
  `config_model`, `from_config(cfg)` and `grant(user, datasource_id)`), with
  `UserContext` and `Grant`.

An adapter is published as an `nl2sql.adapters` entry point, and the engine
finds it there; it never imports an adapter by name.

```bash
pip install nl2sql-sdk
```

Writing an adapter:
[Adapter development](https://nadeem4.github.io/nl2sql/adapters/development/).
Try the engine without installing anything: <https://nl2sql.codewithnk.com>.
