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
  driver: "ODBC Driver 18 for SQL Server" # Optional: unset picks the newest one installed
  trusted_connection: false               # true: log in as the account the engine runs under
  encrypt: true                           # false: a local or test server without TLS
  trust_server_certificate: false         # true: accept a self-signed certificate
```

The keys are `MssqlConnection`'s (`nl2sql.adapters.mssql.adapter`), and any
other key is an error; see [Connection](../configuration/datasources.md#connection).

### Connection Details

* **Driver**: `pyodbc`, with one of Microsoft's ODBC drivers (see [Requirements](#requirements)).
* **ODBC driver**: the `driver` you set, else the newest `ODBC Driver N for SQL Server`
  that `pyodbc.drivers()` lists, so a machine with only Driver 17 works without
  configuration. With none installed, `nl2sql check` fails at the `driver` stage
  and says where to download one.
* **Encryption**: on by default (`Encrypt=yes`). Driver 18 encrypts by default and
  Driver 17 does not, so the adapter always says which it wants: the connection
  behaves the same whichever driver is used. A server with a self-signed
  certificate (a local or test server) needs `trust_server_certificate: true`;
  one without TLS at all needs `encrypt: false`, which sends `Encrypt=no`.
* **URI Constructed**: `mssql+pyodbc://{user}:{pass}@{host}:{port}/{db}?driver={driver}&Encrypt=yes` (`no` with `encrypt: false`), plus
  `Trusted_Connection=yes` and `TrustServerCertificate=yes` when set.
  Built with SQLAlchemy's `URL.create`, so the user name and password are
  percent-escaped: a password containing `@ : / # %` works as written.

## Features

| Feature | Implementation | Note |
| :--- | :--- | :--- |
| **Read-only** | None | Does not declare `ENFORCES_READ_ONLY`; see [Read-only](#read-only). |
| **Timeout** | pyodbc `Connection.timeout` | Client-side query timeout, in whole seconds. Declares `ENFORCES_TIMEOUT`. |
| **Connection stages** | SQL Server's error number | 18456 is `auth`, 4060 `database`, 916 `permission`; ODBC's `08001`, `08S01` and `HYT00` are `network`; no ODBC Driver for SQL Server installed is `driver`. |
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

`pip install "nl2sql-engine[mssql]"` installs `pyodbc`. You also need
Microsoft's ODBC Driver for SQL Server (17 or 18) in your Docker image or local
environment; the adapter uses the newest one installed.

[Download ODBC Driver for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)

```bash
# Debian / Ubuntu
sudo apt-get install unixodbc-dev
```
