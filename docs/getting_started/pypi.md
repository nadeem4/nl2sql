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

The engine reads one config, `nl2sql.yaml`, and the files it includes.
`nl2sql init` writes it (in Python, `nl2sql.config.init_project("my-project")`
writes the same files); see
[Start a config with `nl2sql init`](index.md#start-a-config-with-nl2sql-init).
Each section has its own page:

- [Datasources](../configuration/datasources.md)
- [LLMs](../configuration/llm.md)
- [Embeddings](../configuration/embeddings.md)
- [Policies](../configuration/policies.md)
- [Storage](../configuration/storage.md)
- [Secrets](../configuration/secrets.md)
- [Settings](../configuration/settings.md)

### Building the engine in Python

```python
from nl2sql import NL2SQL

with NL2SQL.from_config("nl2sql.yaml") as engine:   # or $NL2SQL_CONFIG, then ./nl2sql.yaml
    report = engine.check()                         # connects, and reports what is ready
    print(report)
```

Building the engine never connects to a datasource or calls a model.
`engine.check()` does: it raises `EngineNotReady` when a datasource with
`required: true` fails, and otherwise leaves a failed datasource out of
routing. The library never reads a `.env` file; call `python-dotenv` yourself
before `from_config` if you keep keys in one. See
[Public Facade](../api/core/public-facade.md#creating-an-engine) for every way
to build it, including a `Config` built in code and `NL2SQL.demo()`. Use the
config for fixed, app-wide settings. Use runtime registration when you need to
add datasources on a long-running process without restart.

### Datasource configuration

`datasources/` holds one file per datasource; the file name is its id:

```yaml
# datasources/my_postgres.yaml
type: postgres
description: Primary analytics DB.
connection:
  host: localhost
  port: 5432
  user: "${env:POSTGRES_USER}"
  password: "${env:POSTGRES_PASSWORD}"
  database: analytics
limits: { row_limit: 100, timeout_ms: 8000, max_bytes: 10485760 }
```

Install the adapter extras that match `type`:

```bash
pip install "nl2sql-engine[postgres]"
```

See [Adapters](../adapters/index.md) for the full list of available adapters.
See [Datasource config](../configuration/datasources.md) for field definitions.

### LLM configuration

`llm.yaml` selects the model and any per-agent overrides:

```yaml
# llm.yaml
provider: openai
model: gpt-5.4
temperature: 0.0
# api_key is optional: without it OPENAI_API_KEY is read.
agents:
  indexing_enrichment: { model: gpt-5.4-mini }
```

To route chat through [OpenRouter](https://openrouter.ai) instead — one key for
Anthropic, Google, Meta and hundreds of other models, with no extra dependency:

```yaml
provider: openrouter
model: anthropic/claude-sonnet-4.5
temperature: 0.0
```

To use Claude on Anthropic's own API, with the system prompts prompt-cached,
install the `anthropic` extra and point the config at it:

```bash
pip install "nl2sql-engine[anthropic]"
```

```yaml
provider: anthropic
model: claude-opus-5
temperature: null          # Claude Opus 5 rejects a temperature
```

See [LLM configuration → Anthropic](../configuration/llm.md#anthropic-claude).

A local [Ollama](https://ollama.com) daemon is configured the same way, with
`provider: ollama`; how well a given local model handles the pipeline's
structured output varies by model, so see
[LLM configuration → Ollama](../configuration/llm.md#ollama) first.

Note that OpenRouter serves chat completions only. Embeddings are their own
section: `embeddings: { provider: local }` (what `nl2sql init` writes) embeds
with a key-free ONNX model, and `provider: openai` needs `OPENAI_API_KEY`. See
[Embeddings](../configuration/embeddings.md).

See [LLM config](../configuration/llm.md) for full schema details.

### Add a datasource at runtime

You can register a datasource programmatically without editing config files:

```python
from nl2sql import NL2SQL, Datasource, Limits

engine = NL2SQL.from_config("nl2sql.yaml")
engine.datasources.add(Datasource(
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

engine.index.build(["runtime_pg"])
```

The `description` is indexed as given, even though no file lists
`runtime_pg`, and the datasource joins `engine.config`. Indexing makes no LLM call unless you ask for one:
`engine.index.build(["runtime_pg"], enrich=True)` has the
`indexing_enrichment` agent write descriptions for tables and columns the
database left without a comment.

You can also add the datasources of another config:

```python
from nl2sql import NL2SQL, load_config

engine = NL2SQL.from_config("nl2sql.yaml")
for datasource in load_config("other/nl2sql.yaml").datasources:
    engine.datasources.add(datasource)
```

### Replace the LLM at runtime

`engine.llm.replace` swaps the whole `llm` section in the running engine, as
one step: it is validated as `llm.yaml` is, every agent's key must resolve,
and questions already running finish on the old LLM.

```python
from nl2sql import NL2SQL, LLM, AgentLLM

engine = NL2SQL.from_config("nl2sql.yaml")
engine.llm.replace(LLM(provider="openai", model="gpt-5.4",
                       agents={"ast_planner": AgentLLM(model="gpt-5.5")}))
engine.llm.list()["ast_planner"]["model"]   # 'gpt-5.5'
```

The `llm` section of another config:

```python
from nl2sql import load_config

engine.llm.replace(load_config("other/nl2sql.yaml").llm)
```

### Policies configuration

`policies.yaml` defines which roles can access which datasources and tables:

```yaml
# policies.yaml
default_role: viewer
admin_roles: [admin]
roles:
  admin:
    description: System Administrator
    allow: ["*"]
  viewer:
    description: Read-only access to the catalogue
    allow: ["my_postgres.public.products"]
```

`{allow_all: true}` (what `nl2sql init` writes) lets every caller read
everything, for local use only. See
[Policies config](../configuration/policies.md) for the allow and deny patterns and
wildcards.

### Secrets configuration (optional)

A `secrets` section is only needed when you want to pull secrets from a
provider other than the environment:

```yaml
# secrets.yaml
providers:
  - id: azure-prod
    type: azure
    vault_url: "https://my-vault.vault.azure.net/"
```

A datasource then writes `password: "${azure-prod:db-password}"`. See
[Secrets config](../configuration/secrets.md) for supported providers and fields.

## Run a query

```python
from nl2sql import NL2SQL
from nl2sql import UserContext

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
`settings.pipeline.plan_cache: false`; empty it with `nl2sql cache clear`. See
[Determinism → The plan cache](../architecture/determinism.md#the-plan-cache-determinism-from-the-architecture).

## Lifecycle (configure → check → index → query)

1. Configure datasources, LLMs, and policies (`nl2sql init`).
2. Build the engine and call `engine.check()`.
3. Index schemas at least once (and after schema changes).
4. Run queries.

## Index schema (required)

```python
engine.index.build(["my_postgres"])   # one datasource
engine.index.build()                  # every datasource
```

## Configure providers

Secrets providers are installed via extras and selected in the `secrets` section:

```bash
pip install "nl2sql-engine[aws]"
pip install "nl2sql-engine[azure]"
pip install "nl2sql-engine[hashicorp]"
```

See [Configuration](../configuration/system.md) for the whole config, the
environment variables it reads, and where each 0.4 file and variable went.
