# Configuration Reference

The platform uses a **Two-Layer Configuration System**:

1. **Environment Variables**: For global settings, paths, and secrets.
2. **YAML/JSON Files**: For structured data (Datasources, LLMs, Policies).

The `ConfigManager` enforces strict schemas (Pydantic Models) for all files.

## 1. Global Settings (`.env`)

These settings control the startup behavior and file locations.

| Variable | Default | Description |
| :--- | :--- | :--- |
| `OPENAI_API_KEY` | `None` | Required for LLM usage. |
| `VECTOR_STORE` | `./chroma_db` | Path to the vector store persistence directory. |
| `SCHEMA_STORE_BACKEND` | `sqlite` | Schema store backend: `sqlite` or `memory`. |
| `SCHEMA_STORE_PATH` | `./schema_store.db` | SQLite database path for schema store persistence. |
| `DATASOURCE_CONFIG` | `configs/datasources.yaml` | Path to the datasources file. |
| `LLM_CONFIG` | `configs/llm.yaml` | Path to the LLM profile file. |
| `SECRETS_CONFIG` | `configs/secrets.yaml` | Path to the secrets provider file. |
| `POLICIES_CONFIG` | `configs/policies.json` | Path to the RBAC definitions. |
| `ROUTER_L1_THRESHOLD` | `0.4` | Vector search similarity threshold. |
| `OBSERVABILITY_EXPORTER` | `none` | Telemetry exporter: `otlp` (prod), `console` (dev), `none`. |
| `OTEL_EXPORTER_OTLP_ENDPOINT`| `None` | Endpoint for OTeL Collector (e.g. `http://localhost:4317`). |
| `AUDIT_LOG_PATH` | `logs/audit_events.log` | Path for the persistent forensic audit log. |

## 2. Datasources (`datasources.yaml`)

Defines the databases the platform can query.

**Schema:** `List[DatasourceConfig]` wrapped in a file envelope.

```yaml
version: 1
datasources:
  - id: "postgres_prod"
    description: "Main production database"
    connection:
      type: "postgres"
      host: "db.prod.internal"
      port: 5432
      database: "main_db"
      username: "${aws-secrets:prod-db-user}"  # Secret Reference
      password: "${aws-secrets:prod-db-pass}"
    options:
      schema: "public"
      sslmode: "require"
```

* **id**: Unique identifier used in routing and policies.
* **connection**: Driver-specific connection args.
* **options**: Extra kwargs passed to the adapter.

## 3. LLM Profiles (`llm.yaml`)

Defines the models used by different agents.

**Schema:** `LLMFileConfig`

```yaml
version: 1

default:
  provider: "openai"
  model: "gpt-4o"
  temperature: 0.0

agents:
  planner:
    provider: "openai"
    model: "o1-preview"  # Reasoning model for planning
  
  generator:
    provider: "openai"
    model: "gpt-4o"
    temperature: 0.0 # Strict generation
```

* **default**: Fallback config for all agents.
* **agents**: Overrides for specific nodes (`planner`, `generator`, `refiner`, `decomposer`).

## 4. Security Policies (`policies.json`)

Defines Role-Based Access Control (RBAC).

**Schema:** `PolicyFileConfig`

```json
{
  "version": 1,
  "roles": {
    "analyst": {
      "description": "Standard read-only access",
      "role": "analyst",
      "allowed_datasources": ["postgres_prod"],
      "allowed_tables": ["postgres_prod.users", "postgres_prod.orders"]
    },
    "admin": {
      "description": "Full access",
      "role": "admin",
      "allowed_datasources": ["*"],
      "allowed_tables": ["*"]
    }
  }
}
```

* **allowed_tables**: Must be in `datasource.table` format (or `*`).

## 5. Secrets (`secrets.yaml`)

Configures how the app retrieves secrets (e.g., passwords, API keys).

```yaml
version: 1
providers:
  - id: "aws-secrets"
    type: "aws_secrets_manager"
    region_name: "us-east-1"

  - id: "env-vars"
    type: "env"
```

**Usage:** Access secrets in other config files using the syntax `${provider_id:secret_key}`.
