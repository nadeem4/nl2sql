# Configuration

The platform is configured via a combination of **Environment Variables** (for secrets/global settings) and **YAML Config Files** (for structured data).

## Global Settings (`config.yml` / `.env`)

Global settings control the behavior of the core engine.

::: nl2sql.common.settings.Settings

## Datasources (`datasources.yaml`)

Defines the available databases and their connection details.

```yaml
postgres_prod:
  type: postgres
  connection_string: ${env:POSTGRES_URL}
  schema: public
```

## RBAC Policies (`policies.json`)

Defines roles and access rules. (See [Security](../safety/security.md) for details).

## LLM Configuration (`llm.yaml`)

Defines the LLM providers and model parameters for different agents.

```yaml
default:
  provider: openai
  model: gpt-4o

agents:
  planner:
    model: o1-preview  # Uses reasoning model for planning
  generator:
    model: gpt-4o
```
