# Microsoft SQL Server (MSSQL) Adapter

Support for SQL Server 2017+ and Azure SQL.

This adapter extends `BaseSQLAlchemyAdapter` but provides specialized `dry_run` logic using `SET NOEXEC ON` to safely validate T-SQL.

## Configuration

**Type**: `mssql`

```yaml
# datasources/my_db.yaml
type: mssql
connection:
  host: localhost
  port: 1433
  user: sa
  password: "${env:DB_PASS}"
  database: my_db
  driver: "ODBC Driver 17 for SQL Server" # Default
  trusted_connection: false               # true: log in as the account the engine runs under
  trust_server_certificate: false         # true: accept a self-signed certificate (ODBC Driver 18 encrypts by default)
```

The keys are `MssqlConnection`'s (`nl2sql.adapters.mssql.adapter`), and any
other key is an error; see [Connection](../configuration/datasources.md#connection).

### Connection Details

* **Driver**: `pyodbc`. **Requires system ODBC headers installed.**
* **URI Constructed**: `mssql+pyodbc://{user}:{pass}@{host}:{port}/{db}?driver={driver}`, plus
  `Trusted_Connection=yes` and `TrustServerCertificate=yes` when set.
  Built with SQLAlchemy's `URL.create`, so the user name and password are
  percent-escaped: a password containing `@ : / # %` works as written.

## Features

| Feature | Implementation | Note |
| :--- | :--- | :--- |
| **Timeout** | pyodbc `Connection.timeout` | Client-side query timeout, in whole seconds. |
| **Dry Run** | `SET NOEXEC ON` | Validates syntax without execution. |
| **Costing** | `SET SHOWPLAN_XML ON` | Parses XML for `StatementSubTreeCost`. |

### Statement Timeout

SQL Server has no session setting for a statement timeout, so the adapter sets
pyodbc's query timeout (`Connection.timeout`) on every new pooled connection.
pyodbc counts whole seconds, so `limits.timeout_ms` is rounded **up**:
`1500` becomes 2 seconds, and anything from 1 to 1000 becomes 1 second. When the
limit passes, the driver cancels the query and the adapter reports it as a refusal
(`QUERY_REFUSED`), which the engine does not retry.

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
