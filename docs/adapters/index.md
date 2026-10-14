# Supported Adapters

The NL2SQL Platform supports a variety of datasources through specialized adapters. Each adapter is designed to handle the specific idiosyncrasies of its underlying database engine, from connection strings to specialized `EXPLAIN` plans.

## SQL Adapters

We provide first-class support for the following SQL databases via SQLAlchemy.

| Adapter | Description | Status |
| :--- | :--- | :--- |
| **[PostgreSQL](postgres.md)** | Full support including `EXPLAIN`, JSONB, and SSL. | 🟢 Stable |
| **[MySQL](mysql.md)** | Support for 5.7+ and 8.0. Includes `MAX_EXECUTION_TIME` management. | 🟢 Stable |
| **[Microsoft SQL Server](mssql.md)** | Enterprise support via `pyodbc` and `T-SQL` dialect. | 🟡 Beta |
| **[SQLite](sqlite.md)** | File-based local development. | 🟢 Stable |
| **[DuckDB](duckdb.md)** | In-process analytical engine over local files. | 🟢 Stable |

## Core Libraries

For developers building their own adapters, we provide detailed reference documentation for our core SDKs.

* **[SDK Reference](sdk.md)**: The core interface (`DatasourceAdapter`, from `nl2sql-sdk`) that all adapters must implement.
* **[SQLAlchemy Adapter Reference](sqlalchemy.md)**: The helper base class (`BaseSQLAlchemyAdapter`) for building SQL-based adapters.

## Missing your database?

Can't find what you need? Check out the **[Building Adapters](development.md)** guide to see how to implement your own.

## Configuration

All adapters are configured in the `datasources` section of `nl2sql.yaml`, usually
one file per datasource in `datasources/` (see [Datasources](../configuration/datasources.md)).

```yaml
# datasources/sales_db.yaml   (id defaults to the file name)
type: postgres
connection:
  host: "${env:DB_HOST}"
  port: 5432
  # ... see specific adapter docs for full reference
```
