# Configuration Reference

The platform behaves based on three primary configuration files.

## 1. Datasources (`datasources.yaml`)

Defines the database connections.

```yaml
- id: production_db            # Unique Identifier
  engine: postgres             # Adapter type (must match installed adapter)
  sqlalchemy_url: "..."        # Connection string
  description: "Main DB"       # Used by Decomposer for semantic routing
  feature_flags:
    supports_dry_run: true     # Enable Physical Validator features
```

## 2. LLM Config (`llm_config.yaml`)

Configures the AI models.

```yaml
default:
  provider: openai
  model: gpt-4o-mini

agents:
  planner:
    model: gpt-4o    # Use stronger model for complex planning
    temperature: 0.1
```

## 3. Users (`users.json`)

Defines the security context (RBAC).

```json
{
  "default_user": {
    "id": "u_1",
    "role": "analyst",
    "allowed_datasources": ["production_db"],
    "allowed_tables": ["users", "orders"]  # Physical Validator will block access to other tables
  }
}
```
