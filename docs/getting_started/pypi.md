# PyPI (Python API)

Use this path when you want to embed NL2SQL directly in a Python application.
See the Core API documentation at [docs/api/index.md](../api/index.md).

## Install

```bash
# Core only
pip install nl2sql-engine

# install all available adapters. (Never prefer this, until you really need all adapters.)
pip install "nl2sql-engine[all]"

# Core with selected adapters
pip install "nl2sql-engine[postgres]"
pip install "nl2sql-engine[mysql,mssql]"
pip install "nl2sql-engine[duckdb]"

# The browser playground behind `nl2sql demo` (adds fastapi + uvicorn).
# `[all]` does NOT include it -- `all` covers database drivers only.
pip install "nl2sql-engine[demo]"
```

Running a query needs an LLM key; only indexing is key-free. See
[Known limitations](https://github.com/nadeem4/nl2sql#known-limitations).

## Configure

Create config files in your working directory:

- `configs/datasources.yaml`
- `configs/llm.yaml`
- `configs/policies.json`
- `configs/secrets.yaml` (optional)

Start from `configs/datasources.example.yaml` and `configs/policies.example.json`.
There is no example LLM or secrets file; their schemas are linked below.
Detailed schemas for each file:

- [Datasources](../configuration/datasources.md)
- [LLMs](../configuration/llm.md)
- [Policies](../configuration/policies.md)
- [Secrets](../configuration/secrets.md)

!!! note "0.5: `nl2sql init`"
    From 0.5 the engine reads one `nl2sql.yaml` instead of these files.
    `nl2sql init` writes it, and in Python
    `nl2sql.config.init_project("my-project")` writes the same files. See
    [Start a config with `nl2sql init`](index.md#start-a-config-with-nl2sql-init-05).

### Building the engine in Python

In Python the engine is built from one `nl2sql.yaml` (which `nl2sql init`
writes), or from a `Config` built in code. The `configs/` files above are what
the CLI commands still read until they move onto `nl2sql.yaml`.

```python
from nl2sql import NL2SQL

engine = NL2SQL.from_config("nl2sql.yaml")   # or $NL2SQL_CONFIG, then ./nl2sql.yaml
engine.check()                               # connects, and reports what is ready
```

See [Public Facade](../api/core/public-facade.md#creating-an-engine) for every
way to build it. Use the config for fixed, app-wide settings. Use runtime registration when
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
pip install "nl2sql-engine[postgres]"
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
To route chat through [OpenRouter](https://openrouter.ai) instead — one key for
Anthropic, Google, Meta and hundreds of other models, with no extra dependency:

```yaml
version: 1
default:
  provider: openrouter
  model: anthropic/claude-sonnet-4.5
  temperature: 0.0
  api_key: ${env:OPENROUTER_API_KEY}
```

To use Claude on Anthropic's own API, with the system prompts prompt-cached,
install the `anthropic` extra and point the config at it:

```bash
pip install "nl2sql-engine[anthropic]"
```

```yaml
version: 1
default:
  provider: anthropic
  model: claude-opus-5
  temperature: null          # Claude Opus 5 rejects a temperature
  api_key: ${env:ANTHROPIC_API_KEY}
```

See [LLM configuration → Anthropic](../configuration/llm.md#anthropic-claude).

A local [Ollama](https://ollama.com) daemon is configured the same way, with
`provider: ollama` and no `api_key`; how well a given local model handles the
pipeline's structured output varies by model, so see
[LLM configuration → Ollama](../configuration/llm.md#ollama) first.

Note that OpenRouter serves chat completions only. Embeddings are selected
separately with `EMBEDDING_PROVIDER`: the default `openai` means `nl2sql index`
needs `OPENAI_API_KEY` set as well, while `EMBEDDING_PROVIDER=local` embeds with
a key-free ONNX model (see
[System configuration → Embeddings](../configuration/system.md#embeddings)).

See [LLM config](../configuration/llm.md) for full schema details.

### Add a datasource at runtime

You can register a datasource programmatically without editing config files:

```python
from nl2sql import NL2SQL, Datasource, Limits

engine = NL2SQL.from_config("nl2sql.yaml")
engine.add_datasource(Datasource(
    id="runtime_pg",
    type="postgres",
    description="Runtime Postgres datasource",
    connection={
        "host": "localhost",
        "port": 5432,
        "user": "postgres",
        "password": "${env:RUNTIME_PG_PASSWORD}",
        "database": "analytics",
    },
    limits=Limits(timeout_ms=8000, row_limit=100, max_bytes=10485760),
))

engine.index_datasource("runtime_pg")
```

The `description` is indexed as given, even though no file lists
`runtime_pg`, and the datasource joins `engine.config`. Indexing makes no LLM call unless you ask for one:
`engine.index_datasource("runtime_pg", enrich=True)` has the
`indexing_enrichment` agent write descriptions for tables and columns the
database left without a comment.

You can also add the datasources of another config:

```python
from nl2sql import NL2SQL, load_config

engine = NL2SQL.from_config("nl2sql.yaml")
for datasource in load_config("other/nl2sql.yaml").datasources:
    engine.add_datasource(datasource)
```

### Add an LLM at runtime

You can also register or override LLMs programmatically:

```python
from nl2sql import NL2SQL

engine = NL2SQL.from_config("nl2sql.yaml")
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

engine = NL2SQL.from_config("nl2sql.yaml")
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

## Run a query

```python
from nl2sql import NL2SQL
from nl2sql.auth.models import UserContext

engine = NL2SQL.from_config("nl2sql.yaml")
user_ctx = UserContext(user_id="user123", roles=["admin"])

result = engine.run_query(
    "Top 5 customers by revenue last quarter?",
    user_context=user_ctx
)

print(result.final_answer)
for sub_query in result.sub_queries:
    print(sub_query.datasource_id, sub_query.sql)
```

The `QueryResult` includes `sub_queries` (each with its generated `sql`),
`final_answer`, `errors`, `warnings`, `artifact_refs`, and a `trace_id` for
observability. Each sub-query carries a capped sample of its rows (`rows`); the
full result set lives in artifact storage and is addressed through `artifact_refs`.

A question repeated on the same day reuses its validated plan from the plan cache (no planner
call, same SQL and rows; still validated every time), and
`sub_query.plan_source` says `"cache"`. Turn it off with
`PLAN_CACHE_ENABLED=false`; empty it with `nl2sql cache clear`. See
[Determinism → The plan cache](../architecture/determinism.md#the-plan-cache-determinism-from-the-architecture).

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
pip install "nl2sql-engine[aws]"
pip install "nl2sql-engine[azure]"
pip install "nl2sql-engine[hashicorp]"
```

See [Configuration system](../configuration/system.md) for environment variables and defaults.
