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
```

## Fields

- `version`: schema version (currently `1`)
- `datasources`: list of datasource definitions
  - `id`: unique datasource ID
  - `description`: optional human-readable description
  - `connection`: connection details
    - `type`: adapter type (e.g., `postgres`, `mysql`, `mssql`, `sqlite`)
    - adapter-specific fields (host, port, database, driver, etc.)
  - optional limits and metadata (commonly used):
    - `statement_timeout_ms`
    - `row_limit`
    - `max_bytes`
    - `tags`

## Notes

- Use `${env:VAR}` for environment variables or `${provider:key}` for secrets
  resolved via `configs/secrets.yaml`.
- Adapter-specific fields are passed through to the adapter implementation.
