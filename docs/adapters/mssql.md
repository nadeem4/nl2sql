# Microsoft SQL Server (MSSQL) Adapter

Support for SQL Server 2017+ and Azure SQL.

This adapter extends `SQLAlchemyAdapter`. Its row counts are the catalog's (`sys.partitions`), and it enforces `limits.timeout_ms` through pyodbc's query timeout. It **cannot make the session read-only**: see [Read-only](#read-only).

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
| **Read-only** | None | Does not declare `ENFORCES_READ_ONLY`; see [Read-only](#read-only). |
| **Timeout** | pyodbc `Connection.timeout` | Client-side query timeout, in whole seconds. Declares `ENFORCES_TIMEOUT`. |
| **Connection stages** | SQL Server's error number | 18456 is `auth`, 4060 `database`, 916 `permission`; ODBC's `08001`, `08S01` and `HYT00` are `network`. |
| **Row counts** | `sys.partitions` | The row count SQL Server keeps for each table. |
| **Stats** | One query per column | See [Reading a schema](sqlalchemy.md#reading-a-schema). |

### Read-only

SQL Server has no setting that makes a session read-only.
`ApplicationIntent=ReadOnly` only routes the connection to a readable
secondary replica; on a primary the session can still write. So the adapter
does not declare `ENFORCES_READ_ONLY`, and `engine.check()` puts a warning on
every SQL Server datasource's status:

```text
sales: ready
  Warning: sales (mssql): this adapter cannot make the session read-only. Connect with a login that can only read.
```

The typed plan and the validator still block every write (see
[the security model](../security/model.md#what-keeps-the-sql-read-only)). The
third guard is yours: **connect with a login whose only role in the database
is `db_datareader`.**

```sql
CREATE LOGIN nl2sql WITH PASSWORD = '...';
CREATE USER nl2sql FOR LOGIN nl2sql;
ALTER ROLE db_datareader ADD MEMBER nl2sql;
```

### Statement Timeout

SQL Server has no session setting for a statement timeout, so the adapter's
`session_setup` sets pyodbc's query timeout (`Connection.timeout`) on every new
pooled connection.
pyodbc counts whole seconds, so `limits.timeout_ms` is rounded **up**:
`1500` becomes 2 seconds, and anything from 1 to 1000 becomes 1 second. When the
limit passes, the driver cancels the query and the adapter reports it as a timeout
(`QUERY_TIMEOUT`), which the engine does not retry.

## Requirements

You must have the MS ODBC Driver installed in your Docker image or local environment.

[Download ODBC Driver for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)

```bash
# Debian / Ubuntu
sudo apt-get install unixodbc-dev
```
