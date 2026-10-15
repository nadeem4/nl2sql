# MySQL Adapter

Support for MySQL 5.7+ and 8.0+.

!!! info "Implementation"
    This adapter extends `SQLAlchemyAdapter`. Its `session_setup` makes every new pooled connection read-only and sets the `MAX_EXECUTION_TIME` session variable.

## Configuration

**Type**: `mysql`

```yaml
# datasources/my_db.yaml
type: mysql
connection:
  host: localhost
  port: 3306
  user: root
  password: "${env:DB_PASS}"
  database: my_db
```

The keys are `MysqlConnection`'s (`nl2sql.adapters.mysql.adapter`), and any
other key is an error; see [Connection](../configuration/datasources.md#connection).

### Connection Details

* **Driver**: `pymysql` (Pure Python), with `cryptography`. MySQL 8's default
  login, `caching_sha2_password`, needs `cryptography` on a connection without
  TLS. `pip install "nl2sql-engine[mysql]"` installs both (`pymysql[rsa]`);
  without `cryptography`, `nl2sql check` fails at the `driver` stage with that
  install command.
* **URI Constructed**: `mysql+pymysql://{user}:{pass}@{host}:{port}/{db}`.
  Built with SQLAlchemy's `URL.create`, so the user name and password are
  percent-escaped: a password containing `@ : / # %` works as written.

## Features

| Feature | Implementation | Note |
| :--- | :--- | :--- |
| **Read-only** | `SET SESSION TRANSACTION READ ONLY` | An `INSERT` or `CREATE TABLE` fails even for a login that could write. Declares `ENFORCES_READ_ONLY`. |
| **Timeout** | `SET SESSION MAX_EXECUTION_TIME = {ms}` | Server-side. It applies to `SELECT`, which is all the engine runs. Declares `ENFORCES_TIMEOUT`. |
| **Connection stages** | MySQL's error number | 1045 is `auth`, 1049 `database`, 1044 `permission`, 2003 and 2005 `network`; PyMySQL's "'cryptography' package is required" is `driver`. |
| **Estimates** | `EXPLAIN FORMAT=JSON` | `estimate` never runs the query. Each table access counts `rows_examined_per_scan`, MySQL's estimate of the rows it reads, so a full scan (`access_type: ALL`) counts the table; a derived table is not counted again. `cost` is `query_cost`. Runs read-only, under `MAX_EXECUTION_TIME`. Declares `ESTIMATES_COST`. |
| **Row counts** | `information_schema.tables.table_rows` | InnoDB's estimate; `ANALYZE TABLE` refreshes it. |
| **Stats** | One query per column | See [Reading a schema](sqlalchemy.md#reading-a-schema). |
