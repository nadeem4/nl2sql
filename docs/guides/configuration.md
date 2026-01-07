# Configuration Guide

The NL2SQL Platform uses a strict, type-safe configuration system (Schema V3) defined in `datasources.yaml`.

## 1. Quick Start

Run the setup wizard to generate a valid configuration:

```bash
nl2sql setup
```

## 2. Secrets Management

Sensitive credentials should never be stored in plaintext. We support environment variable expansion using the `${env:VAR_NAME}` syntax.

**Example**:

```yaml
connection:
  host: localhost
  password: ${env:DB_PASSWORD}  # Reads OS environment variable 'DB_PASSWORD'
```

## 3. Supported Databases

### PostgreSQL

```yaml
- id: my_postgres
  connection: 
    type: postgres
    host: localhost
    port: 5432
    user: admin
    password: ${env:PG_PASS}
    database: analytics
    # Options: require, prefer, verify-full
    ssl_mode: prefer
```

### MySQL

```yaml
- id: my_mysql
  connection:
    type: mysql
    user: admin
    password: ${env:MYSQL_PASS}
    database: ecommerce
    # Optional: Use Unix Socket for local connection
    unix_socket: /var/run/mysqld/mysqld.sock
```

### SQL Server (MSSQL / Azure)

Supports Standard, Windows, and Azure Authentication.

**Standard**:

```yaml
- id: mssql_std
  connection:
    type: mssql
    host: sql.example.com
    user: sa
    password: ${env:SA_PASS}
    database: master
```

**Azure Service Principal**:

```yaml
- id: azure_db
  connection:
    type: mssql
    host: my-server.database.windows.net
    database: core_db
    authentication: azure_sp
    # Service Principal Credentials
    client_id: ${env:AZURE_CLIENT_ID}
    client_secret: ${env:AZURE_CLIENT_SECRET}
    tenant_id: ${env:AZURE_TENANT_ID}
```

### SQLite

```yaml
- id: local_sqlite
  connection:
    type: sqlite
    database: /abs/path/to/db.sqlite
```

## 4. Safety Limits

To prevent "Out of Memory" (OOM) crashes and protect the LLM context window, we enforce strict limits on query results.

| Field | Default | Description |
| :--- | :--- | :--- |
| `row_limit` | 1000 | Max rows returned by a query. |
| `max_bytes` | 10MB | **Hard Limit** on payload size. Calculated via strict JSON serialization size. Queries exceeding this will fail safely. |

**Example**:

```yaml
options:
  row_limit: 500
  max_bytes: 5242880 # 5MB limit
```

## 5. IDE Support (VS Code)

Your `datasources.yaml` includes a header that enables **Autocomplete** and **Validation** in VS Code:

```yaml
# yaml-language-server: $schema=./datasources.schema.json
```

Do not remove this line. It ensures your configuration matches the strict Pydantic models used by the engine.
