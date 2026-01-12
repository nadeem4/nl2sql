# Microsoft SQL Server (MSSQL) Adapter

Support for SQL Server 2017+ and Azure SQL.

This adapter extends `BaseSQLAlchemyAdapter` but provides specialized `dry_run` logic using `SET NOEXEC ON` to safely validate T-SQL.

## Configuration

**Type**: `mssql`

```yaml

connection:
  type: "mssql"
  host: "localhost"
  port: 1433
  user: "sa"
  password: "${env:DB_PASS}"
  database: "my_db"
  driver: "ODBC Driver 17 for SQL Server" # Default
  trusted_connection: false
```

### Connection Details

* **Driver**: `pyodbc`. **Requires system ODBC headers installed.**
* **URI Constructed**: `mssql+pyodbc://{user}:{pass}@{host}:{port}/{db}?driver={driver}`

## Features

| Feature | Implementation | Note |
| :--- | :--- | :--- |
| **Timeout** | Not strictly enforced by driver | Rely on global statement timeout. |
| **Dry Run** | `SET NOEXEC ON` | Validates syntax without execution. |
| **Costing** | `SET SHOWPLAN_XML ON` | Parses XML for `StatementSubTreeCost`. |

### Optimization Details

* **Dry Run**: Uses `SET NOEXEC ON`. This is a native T-SQL session setting that compiles the query but ensures it is **not executed**. This is extremely safe and accurate for validation.
* **Explain**: Uses `SET SHOWPLAN_XML ON` to retrieve the execution plan in XML format.
* **Cost Estimate**: Parses the XML plan to find `StatementSubTreeCost` (estimated cost) and `StatementEstRows` (estimated rows).

## Requirements

You must have the MS ODBC Driver installed in your Docker image or local environment.

[Download ODBC Driver for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)

```bash
# Debian / Ubuntu
sudo apt-get install unixodbc-dev
```
