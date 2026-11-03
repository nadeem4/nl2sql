# nl2sql-engine

The engine behind NL2SQL, for developers who embed it in Python or run it through the CLI and REST packages. The model returns a typed query plan, never SQL text; the plan is checked against the real schema and the caller's role before any SQL is generated.

This distribution holds the pipeline, the `nl2sql` Python facade and the database adapters (PostgreSQL, MySQL, SQL Server, SQLite, DuckDB). The `nl2sql` command is the separate `nl2sql-cli` package and the REST server and web app are `nl2sql-api`.

## Try it

Hosted demo on three sample databases, nothing to install: <https://nl2sql.codewithnk.com>. To run it yourself:

```bash
pip install "nl2sql-cli[web]"
nl2sql demo
```

## Use it from Python

```python
from nl2sql import NL2SQL, UserContext

engine = NL2SQL.demo()      # the bundled databases; reads a provider key from the environment
engine.index.build()        # once: builds the search index
result = engine.run_query(
    "How many customers are there?",
    user_context=UserContext(roles=["admin"]),
)
print(result.final_answer["summary"] if result.final_answer else result.errors)
```

Your own config instead: `NL2SQL.from_config("nl2sql.yaml")`. Database drivers are extras (`pip install "nl2sql-engine[postgres]"`, `[mysql]`, `[mssql]`, `[duckdb]`, `[all]`); SQLite needs none.

## Documentation

Full documentation: <https://nadeem4.github.io/nl2sql/>

- [Get started](https://nadeem4.github.io/nl2sql/get-started/)
- [How it works](https://nadeem4.github.io/nl2sql/how-it-works/overview/)
- [Security model](https://nadeem4.github.io/nl2sql/security/model/)
- [Known limitations](https://nadeem4.github.io/nl2sql/reference/limitations/)

`nl2sql-sdk`, `nl2sql-engine`, `nl2sql-api` and `nl2sql-cli` are released together. MIT licensed.
