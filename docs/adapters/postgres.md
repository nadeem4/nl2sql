# PostgreSQL Adapter

This adapter extends `SQLAlchemyAdapter`, leveraging automatic schema reflection and statistics gathering. Its row counts are the catalog's estimates (`pg_class.reltuples`), so a table never analyzed has none.

## Configuration

**Type**: `postgres`

```yaml
# datasources/my_db.yaml
type: postgres
connection:
  host: localhost
  port: 5432
  user: postgres
  password: "${env:DB_PASS}"
  database: my_db
  sslmode: require # disable, prefer (default), require or verify-full
```

The keys are `PostgresConnection`'s
(`nl2sql.adapters.postgres.adapter`), and any other key is an error; see
[Connection](../configuration/datasources.md#connection).

### Connection Details

* **Driver**: `psycopg2` (via `sqlalchemy`).
* **URI Constructed**: `postgresql+psycopg2://{user}:{pass}@{host}:{port}/{db}?sslmode={sslmode}`.
  Built with SQLAlchemy's `URL.create`, so the user name and password are
  percent-escaped: a password containing `@ : / # %` works as written.

## Features

| Feature | Implementation | Note |
| :--- | :--- | :--- |
| **Read-only** | `SET default_transaction_read_only = on` | Set on every new pooled connection (`session_setup`): an `INSERT` or `CREATE TABLE` fails even for a login that could write. Declares `ENFORCES_READ_ONLY`. |
| **Timeout** | `SET statement_timeout = {ms}` | Set with the read-only flag; enforced server-side. Declares `ENFORCES_TIMEOUT`. |
| **Connection stages** | libpq's message | psycopg2 has no SQLSTATE for a failed connection, so `classify` reads the message: a refused password is `auth`, a missing database `database`, an unreachable host or port `network`. |
| **Estimates** | `EXPLAIN (FORMAT JSON, VERBOSE)` | `estimate` never runs the query. A `Seq Scan` counts its whole relation, not its `Plan Rows` (the rows left after the filter): the `Plan Rows` of an unfiltered scan of it, which the planner makes from `pg_class.reltuples` scaled to the relation's current size, so a relation never analyzed (on any version) is counted too. `Index Scan`, `Index Only Scan` and `Bitmap Index Scan` count their `Plan Rows`. A scan on the inner side of a `Nested Loop` counts once per outer row, unless a `Materialize` keeps its rows. Not counted: `Foreign Scan`, `Function Scan`, and the repeats of a correlated `SubPlan`. `cost` is the plan's `Total Cost`. Runs read-only, under `statement_timeout`. Declares `ESTIMATES_COST`. |
| **Row counts** | `pg_class.reltuples` | The catalog's estimate; a table never analyzed has none, and is counted under `profile: stats`. |
| **Stats** | One query per column | Null share, distinct count, `min/max` (no `min/max` for `boolean`, `uuid`; only the null share for `jsonb`, `bytea`, arrays). A column that fails is an index issue, rolled back so the transaction is not aborted; see [Reading a schema](sqlalchemy.md#reading-a-schema). |

## Troubleshooting

### SSL Verification

If connecting to Azure or AWS RDS with strict SSL, ensure you pass the CA certificate path through the standard libpq environment variables (`PGSSLROOTCERT`), or use `sslmode: disable` for testing.
