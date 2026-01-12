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

## Missing your database?

Can't find what you need? Check out the **[Building Adapters](development.md)** guide to see how to implement your own.

## Configuration

All adapters are configured in your `configs/datasources.yaml` file.

```yaml
version: 1
datasources:
  - id: "sales_db"
    connection:
      type: "postgres"
      host: "${env:DB_HOST}"
      port: 5432
      # ... see specific adapter docs for full reference
```
