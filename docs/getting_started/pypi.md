# PyPI (Python API)

Use this path when you want to embed NL2SQL directly in a Python application.
See the Core API documentation at [docs/api/index.md](../api/index.md).

## Install

```bash
# Core only
pip install nl2sql-core

# install all available adapters. (Never prefer this, until you really need all adapters.)
pip install "nl2sql-core[all]"

# Core with selected adapters
pip install "nl2sql-core[postgres]"
pip install "nl2sql-core[mysql,mssql]"
```

## Configure

Create config files in your working directory:

- `configs/datasources.yaml`
- `configs/llm.yaml`
- `configs/policies.json`
- `configs/secrets.yaml` (optional)

Start from the examples in `configs/datasources.example.yaml`, `configs/llm.demo.yaml`,
and `configs/policies.example.json`.
Detailed schemas for each file:

- [Datasources](../configuration/datasources.md)
- [LLMs](../configuration/llm.md)
- [Policies](../configuration/policies.md)
- [Secrets](../configuration/secrets.md)

### Configuration paths at startup

You can pass config paths directly when creating the engine:

```python
from nl2sql import NL2SQL

engine = NL2SQL(
    ds_config_path="configs/datasources.yaml",
    llm_config_path="configs/llm.yaml",
    secrets_config_path="configs/secrets.yaml",
    policies_config_path="configs/policies.json"
)
```

Use startup configuration for fixed, app-wide settings. Use runtime registration when
you need to add or override datasources/LLMs per request, per tenant, or on a
long-running process without restart.

### Datasource configuration

`configs/datasources.yaml` defines one or more database connections and limits:

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

Install the adapter extras that match `connection.type`:

```bash
pip install "nl2sql-core[postgres]"
```

See [Adapters](../adapters/index.md) for the full list of available adapters.
See [Datasource config](../configuration/datasources.md) for field definitions.

### LLM configuration

`configs/llm.yaml` selects the default model and any per-agent overrides:

```yaml
version: 1
default:
  provider: openai
  model: gpt-5.2
  temperature: 0.0
  api_key: ${env:OPENAI_API_KEY}
agents:
  indexing_enrichment:
    provider: openai
    model: gpt-5.2
    temperature: 0.0
    api_key: ${env:OPENAI_API_KEY}
```
See [LLM config](../configuration/llm.md) for full schema details.

### Add a datasource at runtime

You can register a datasource programmatically without editing config files:

```python
from nl2sql import NL2SQL

engine = NL2SQL()
engine.add_datasource({
    "id": "runtime_pg",
    "description": "Runtime Postgres datasource",
    "connection": {
        "type": "postgres",
        "host": "localhost",
        "port": 5432,
        "user": "postgres",
        "password": "postgres",
        "database": "analytics"
    },
    "statement_timeout_ms": 8000,
    "row_limit": 100,
    "max_bytes": 10485760
})

engine.index_datasource("runtime_pg")
```

You can also register datasources from a config file at runtime:

```python
from nl2sql import NL2SQL

engine = NL2SQL()
engine.add_datasource_from_config("configs/datasources.yaml")
```

### Add an LLM at runtime

You can also register or override LLMs programmatically:

```python
from nl2sql import NL2SQL

engine = NL2SQL()
engine.configure_llm({
    "name": "default",
    "provider": "openai",
    "model": "gpt-5.2",
    "temperature": 0.0,
    "api_key": "${env:OPENAI_API_KEY}"
})
```

You can also load LLMs from a config file at runtime:

```python
from nl2sql import NL2SQL

engine = NL2SQL()
engine.configure_llm_from_config("configs/llm.yaml")
```

### Policies configuration (RBAC)

`configs/policies.json` defines which roles can access which datasources/tables:

```json
{
  "version": 1,
  "roles": {
    "admin": {
      "description": "System Administrator",
      "role": "admin",
      "allowed_datasources": ["*"],
      "allowed_tables": ["*"]
    }
  }
}
```
See [Policies config](../configuration/policies.md) for allowed table formats and wildcards.

### Secrets configuration (optional)

`configs/secrets.yaml` is only needed when you want to pull secrets from a provider:

```yaml
version: 1
providers:
  # - id: azure-prod
  #   type: azure
  #   vault_url: "https://my-vault.vault.azure.net/"
```
See [Secrets config](../configuration/secrets.md) for supported providers and fields.

Secrets providers are installed via extras and selected in `configs/secrets.yaml`:

## Run a query

```python
from nl2sql import NL2SQL
from nl2sql.auth.models import UserContext

engine = NL2SQL()
user_ctx = UserContext(user_id="user123", roles=["admin"])

result = engine.run_query(
    "Top 5 customers by revenue last quarter?",
    user_context=user_ctx
)

print(result.final_answer)
print(result.sql)
```

The `QueryResult` includes `sql`, `results`, `final_answer`, `errors`,
`warnings`, and a `trace_id` for observability.

## Lifecycle (configure → index → query)

1. Configure datasources, LLMs, and policies.
2. Index schemas at least once (and after schema changes).
3. Run queries.

## Index schema (required)

```python
engine.index_datasource("my_postgres")
engine.index_all_datasources()
```

## Configure providers

Secrets providers are installed via extras and selected in `configs/secrets.yaml`:

```bash
pip install "nl2sql-core[aws]"
pip install "nl2sql-core[azure]"
pip install "nl2sql-core[hashicorp]"
```

See [Configuration system](../configuration/system.md) for environment variables and defaults.
