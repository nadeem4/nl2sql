# Configuration Guide

The NL2SQL Platform uses a strict, type-safe configuration system (Schema V3) defined in `datasources.yaml`.

## 1. Quick Start

Run the setup wizard to generate a valid configuration:

```bash
nl2sql setup
```

## 2. Secrets Management

Sensitive credentials should never be stored in plaintext. We support **strict** variable expansion using the `${provider_id:key}` syntax, powered by `secrets.yaml`.

### 2.1 Configuration (`secrets.yaml`)

Define your secret providers in a `secrets.yaml` file (or `configs/secrets.yaml`).

```yaml
version: 1
providers:
  - id: azure-main
    type: azure
    vault_url: "https://my-vault.vault.azure.net/"
    # You can resolve credentials from ENV here (Two-Phase Loading)
    client_secret: "${env:AZURE_CLIENT_SECRET}"
    
  - id: aws-prod
    type: aws
    region_name: us-east-1
```

### 2.2 Usage (`datasources.yaml`)

Reference the secrets using the `id` defined above.

> [!IMPORTANT]
>
> - **Strict Syntax**: Format must be exactly `${provider_id:key}`.
> - **Provider ID**: Matches the `id` field in `secrets.yaml`.
> - **Environment**: `${env:VAR}` is always available without config.

**Example**:

```yaml
connection:
  host: localhost
  # Uses 'aws-prod' provider defined in secrets.yaml
  password: ${aws-prod:db/password}
  # Uses built-in env provider
  user: ${env:DB_USER}
```

**Example**:

```yaml
connection:
  host: localhost
  password: ${env:DB_PASSWORD}  # Valid
  # invalid_host: my-db-${env:ID}.com  <-- Partial interpolation is NOT supported
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

> [!NOTE]
> The `connection.type` field determines which **Adapter** loads the datasource. For standard SQL databases, this matches the **SQLAlchemy Dialect** (e.g., `postgres`, `mysql`, `mssql`, `sqlite`). Custom adapters may define their own types.

## 4. Safety Limits

To prevent "Out of Memory" (OOM) crashes and protect the LLM context window, we enforce strict limits on query results.

| Field | Default | Description |
| :--- | :--- | :--- |
| `row_limit` | 1000 | Max rows returned by a query. |
| `max_bytes` | 10MB | **Hard Limit** on payload size. Calculated via efficient row sampling (avg of first 50 rows). Queries exceeding this will fail safely. |

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

## 6. Environment Variables

You can configure the application using the following environment variables (defined in `.env` or system environment).

| Variable | Default | Description |
| :--- | :--- | :--- |
| `OPENAI_API_KEY` | - | **Required** for LLM and Embedding services. |
| `DATASOURCE_CONFIG` | `configs/datasources.yaml` | Path to the datasource configuration file. |
| `SECRETS_CONFIG` | `configs/secrets.yaml` | Path to the secrets configuration file. |
| `LLM_CONFIG` | `configs/llm.yaml` | Path to the LLM model configuration file. |
| `POLICIES_CONFIG` | `configs/policies.json` | Path to the RBAC policies file. |
| `VECTOR_STORE` | `./chroma_db` | Path (directory) to persist the vector store. |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI embedding model name. |
| `BENCHMARK_CONFIG` | `configs/benchmark_suite.yaml` | Path to accurate testing suite configuration. |
| `ROUTING_EXAMPLES` | `configs/sample_questions.yaml` | Path to examples used for few-shot routing. |
| `ROUTER_L1_THRESHOLD` | `0.4` | Threshold for Vector Search relevance. |
| `ROUTER_L2_THRESHOLD` | `0.6` | Threshold for Multi-Query voting agreement. |
