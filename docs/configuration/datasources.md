# Datasource Configuration

Datasource configuration lives in `configs/datasources.yaml` and declares one or
more database connections that NL2SQL can query.

## File structure

```yaml
version: 1
datasources:
  - id: my_postgres
    description: "Primary analytics DB."
    connection:
      type: postgres
      host: localhost
      port: 5432
      user: ${env:POSTGRES_USER}
      password: ${env:POSTGRES_PASSWORD}
      database: analytics
    statement_timeout_ms: 8000
    row_limit: 100
    max_bytes: 10485760
    preferred_tables: [v_customer_revenue, v_monthly_sales]
```

## Fields

- `version`: schema version (currently `1`)
- `datasources`: list of datasource definitions
  - `id`: unique datasource ID
  - `description`: optional human-readable description
  - `connection`: connection details
    - `type`: adapter type (e.g., `postgres`, `mysql`, `mssql`, `sqlite`, `duckdb`)
    - adapter-specific fields (host, port, database, driver, etc.)
  - optional limits and metadata (commonly used). The three limits
    (`statement_timeout_ms`, `row_limit`, `max_bytes`) go beside `connection`, as
    above, or under an `options:` map; the two spellings are equivalent. If a
    limit is set in both places, the lower value wins.
    - `statement_timeout_ms`: the longest one statement may run. Postgres,
      MySQL, SQL Server and DuckDB enforce it (SQL Server rounds up to whole
      seconds); SQLite does not, and uses it only as its lock-wait timeout. See
      each [adapter page](../adapters/index.md) for how.
    - `row_limit`: the most rows one sub-query returns. Unset, it is
      `RESULT_ROW_LIMIT` (100,000); set higher, `RESULT_ROW_LIMIT` still bounds it.
      A result that reaches it is flagged as truncated and the answer says so. It
      is applied before results are combined, so a join input that reaches it
      is refused (`RESULT_TRUNCATED`) and a union input that reaches it is
      flagged with a warning; see [row cap before a combine](../architecture/nodes/engine_aggregator_node.md#row-cap-before-a-combine).
    - `max_bytes`
    - `tags`
  - `preferred_tables`: optional list of tables or views the planner should
    reach for first, usually views the data team built for common questions.
    A name is the bare table name or `schema.table`, case ignored. See
    [Preferred tables](#preferred-tables).

## Preferred tables

The package never creates a view. The data team creates it in the database,
lists it under `preferred_tables`, and runs `nl2sql index`; every built-in
adapter snapshots views as well as tables (PostgreSQL materialized views are
not read). At question time the schema retriever lists preferred tables first
and marks them `"preferred": true` in the schema the planner sees, and the
planner is told to plan against a preferred table when it answers the
question:

- When the whole schema is sent (`SCHEMA_RETRIEVAL_FULL_SNAPSHOT_MAX_TABLES`
  tables or fewer), every preferred table is in it, first.
- Otherwise the retrieved tables are re-ordered so preferred ones come first,
  and a preferred table the search scored but did not pick is added when its
  similarity is within 0.05 of the least similar pick.

RBAC is unchanged: a preferred table the role may not read loses its data like
any other table, and the validator refuses a plan that reads it.

A name that is not in the indexed schema is a warning, not an error: `nl2sql
doctor` and the index status (`NL2SQL.index_health`, `GET /api/v1/index/status`)
print "Preferred table 'v_x' of 'chinook' is not in the indexed schema". The
usual cause is a view created after the last index; re-index the datasource.

## When a datasource fails to start

A datasource that cannot start (an unknown `type`, a missing driver, an invalid
connection config, a `${...}` reference that does not resolve) does not stop the
engine. At startup the others register and the broken one is marked
unavailable:

- the log has an error: `Datasource 'sales' is unavailable: <reason>`;
- it is not in `list_datasources()` (or `GET /api/v1/datasource`);
  `NL2SQL.list_unavailable_datasources()` maps it to the reason;
- the index status (`NL2SQL.index_health()`, `GET /api/v1/index/status`) carries
  the same sentence as a warning;
- a query with `datasource_id` set to it, and `nl2sql index` or
  `rebuild_index(datasource_ids=[...])` naming it, fail with that reason;
- `nl2sql doctor` shows it as `Failed` in the connectivity table, with the
  reason;
- `nl2sql policy validate` reports a role that names it as `UNAVAILABLE`.

If datasources are configured and none of them can start, startup fails with
every reason. There is no setting yet to make one bad datasource fatal; run
`nl2sql doctor` in a deploy check if you need that.

A secret provider in `secrets.yaml` that cannot start is different: it fails
startup. See [Secrets → When a provider fails to start](secrets.md#when-a-provider-fails-to-start).

## Notes

- Use `${env:VAR}` for environment variables or `${provider:key}` for secrets
  resolved via `configs/secrets.yaml`.
- Adapter-specific fields are passed through to the adapter implementation.
