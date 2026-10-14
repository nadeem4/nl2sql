# PostgreSQL Adapter

The Postgres adapter is the **Gold Standard** adapter for the platform. It supports the full set of optimization features including `EXPLAIN`-based dry runs and cost estimation.

This adapter extends `BaseSQLAlchemyAdapter`, leveraging automatic schema reflection and statistics gathering.

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
| **Timeout** | Native `-c statement_timeout={ms}` | Enforced server-side. |
| **Dry Run** | `EXPLAIN {sql}` | Highly accurate validation. |
| **Costing** | `EXPLAIN (FORMAT JSON) {sql}` | Returns "Total Cost" and "Plan Rows". |
| **Stats** | Optimized Queries | Fetches `null_perc`, `distinct`, `min/max` (no `min/max` for `boolean`, `uuid`; none for `jsonb`, `bytea`, arrays). A column that fails is skipped without aborting the transaction; see [Column statistics](sqlalchemy.md#column-statistics). |

### Optimization Details

The Postgres adapter leverages native `EXPLAIN` capabilities for robust validation and estimation:

* **Dry Run**: Implemented via `EXPLAIN {sql}`. This validates the SQL syntax and ensures that all tables/columns exist without actually executing the query.
* **Explain**: Uses `EXPLAIN (FORMAT JSON) {sql}` to retrieve the full query execution plan in structured JSON format.
* **Cost Estimate**: Uses the same `EXPLAIN (FORMAT JSON) {sql}` command. It parses the root `Plan` object to extract:
  * `Total Cost`: Used as the query cost proxy.
  * `Plan Rows`: Used as the estimated result size.

## Troubleshooting

### SSL Verification

If connecting to Azure or AWS RDS with strict SSL, ensure you pass the CA certificate path through the standard libpq environment variables (`PGSSLROOTCERT`), or use `sslmode: disable` for testing.
