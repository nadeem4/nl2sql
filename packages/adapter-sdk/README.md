# nl2sql-adapter-sdk

The contracts a database adapter for the
[nl2sql engine](https://github.com/nadeem4/nl2sql) implements. It depends on
pydantic and the standard library only, so an adapter built against it inherits
nothing else.

- `DatasourceAdapterProtocol`: `capabilities()`, `connect()`,
  `fetch_schema_snapshot()`, `execute(request)` and `get_dialect()`, which
  returns a sqlglot dialect name such as `postgres` or `tsql`.
- `AdapterRequest`, `ResultFrame` and `ResultError`: what the engine sends an
  adapter and what it gets back.
- `SchemaSnapshot` and the table, column and foreign-key contracts an adapter
  describes its schema with.

An adapter is published as an `nl2sql.adapters` entry point, and the engine
finds it there; it never imports an adapter by name.

```bash
pip install nl2sql-adapter-sdk
```

Writing an adapter:
[Adapter development](https://nadeem4.github.io/nl2sql/adapters/development/).
Try the engine without installing anything: <https://nadeem4nk-nl2sql-demo.hf.space>.
