# nl2sql-sdk

The interfaces an extension for the
[nl2sql engine](https://github.com/nadeem4/nl2sql) implements: database
adapters and secret providers. The import package is `nl2sql_sdk`. It depends
on pydantic and the standard library only, so an extension built against it
inherits nothing else.

- `DatasourceAdapterProtocol`: `capabilities()`, `connect()`,
  `fetch_schema_snapshot()`, `execute(request)` and `get_dialect()`, which
  returns a sqlglot dialect name such as `postgres` or `tsql`. An adapter class
  also declares `interface_version = 1`, the `driver_modules` it needs and an
  `install_hint` for when one is missing.
- `AdapterRequest`, `ResultFrame` and `ResultError`: what the engine sends an
  adapter and what it gets back.
- `SchemaSnapshot` and the table, column and foreign-key contracts an adapter
  describes its schema with.
- `SecretProvider`: `interface_version = 1`, a strict `config_model` for its
  block in the secrets config, `from_config(cfg)` and `get_secret(key)`.

An adapter is published as an `nl2sql.adapters` entry point, and the engine
finds it there; it never imports an adapter by name.

```bash
pip install nl2sql-sdk
```

Writing an adapter:
[Adapter development](https://nadeem4.github.io/nl2sql/adapters/development/).
Try the engine without installing anything: <https://nl2sql.codewithnk.com>.
