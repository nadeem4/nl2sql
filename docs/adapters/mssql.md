# Microsoft SQL Server (MSSQL) Adapter

Support for SQL Server 2017+ and Azure SQL.

This adapter extends `SQLAlchemyAdapter`. Its row counts are the catalog's (`sys.partitions`), and it enforces `limits.timeout_ms` through pyodbc's query timeout.

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
| **Row counts** | `sys.partitions` | The row count SQL Server keeps for each table. |
| **Stats** | One query per column | See [Reading a schema](sqlalchemy.md#reading-a-schema). |

### Statement Timeout

SQL Server has no session setting for a statement timeout, so the adapter sets
pyodbc's query timeout (`Connection.timeout`) on every new pooled connection.
pyodbc counts whole seconds, so `limits.timeout_ms` is rounded **up**:
`1500` becomes 2 seconds, and anything from 1 to 1000 becomes 1 second. When the
limit passes, the driver cancels the query and the adapter reports it as a refusal
(`QUERY_REFUSED`), which the engine does not retry.

## Requirements

You must have the MS ODBC Driver installed in your Docker image or local environment.

[Download ODBC Driver for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)

```bash
# Debian / Ubuntu
sudo apt-get install unixodbc-dev
```
