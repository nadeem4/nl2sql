# Configuration System

nl2sql reads one config: `nl2sql.yaml` and the files it includes. Loading it
gives one typed `Config` object, and building that same `Config` in Python
gives exactly the same engine. Everything the engine needs is in it. Only
the config path and the provider key variables are read directly (see
[Environment variables](#environment-variables)); any other value from the
environment is written in the YAML as `${env:VAR}`.

This page is the reference.

## `nl2sql.yaml`

`nl2sql init` writes a new project, with each section in its own file:

```bash
nl2sql init my-project              # an example SQLite datasource, allow_all policies
nl2sql init my-project --demo       # the demo: three sample databases, roles, verified queries
nl2sql init my-project --provider anthropic
```

```text
nl2sql.yaml            # the table of contents
datasources/
  example.yaml         # one file per datasource (--demo: chinook.yaml, support.yaml, webanalytics.yaml)
llm.yaml               # provider + model; no key, the provider's standard variable is read
embeddings.yaml        # provider: local
policies.yaml          # allow_all: true            (--demo: admin, analyst, viewer roles)
storage.yaml           # path: .nl2sql
knowledge.yaml         # one yaml knowledge source: glossary.yaml
glossary.yaml          # one empty block per datasource (--demo: the Chinook glossary)
curated.yaml           # --demo only: the demo's verified queries
settings.yaml          # --demo only: the demo's tuning knobs
data/                  # --demo only: the three SQLite files, copied
.gitignore             # .nl2sql/ and .env
```

`init` refuses to overwrite a file without `--force`, never writes a key, and
checks what it wrote with `load_config`.

```yaml
# nl2sql.yaml
version: 2
datasources: { file: datasources/ }      # a folder, one file per datasource
llm:         { file: llm.yaml }
embeddings:  { file: embeddings.yaml }
policies:    { file: policies.yaml }
storage:     { file: storage.yaml }
curated:     { file: curated.yaml }      # optional
secrets:     { file: secrets.yaml }      # optional
settings:    { file: settings.yaml }     # optional
api:         { file: api.yaml }          # optional, read by nl2sql-api only
observers:   { file: observers.yaml }    # optional: where events, logs and metrics go
hooks:       { file: hooks.yaml }        # optional
evals:       { file: evals.yaml }        # optional
knowledge:   { file: knowledge.yaml }    # optional: where the glossary comes from (0.8)
conversations: { file: conversations.yaml }  # optional: follow-up questions (0.9)
chat_tools:  { file: chat_tools.yaml }   # optional: which chat tools are on (0.9)
dashboards:  { file: dashboards.yaml }   # optional (0.9)
mcp:         { file: mcp.yaml }          # optional: nl2sql mcp --http (0.9)
```

A small project can write every section inline instead. This file builds the
same `Config` as a split layout would:

```yaml
version: 2
datasources:
  - id: shop
    type: sqlite
    connection: { database: shop.db }
llm:        { provider: anthropic, model: claude-sonnet-5-5 }
embeddings: { provider: local }
policies:   { allow_all: true }
storage:    { path: .nl2sql }
```

| Section | Required | Page | Notes |
| --- | --- | --- | --- |
| `version` | yes | | `2`. Anything else, or none, is an error. |
| `datasources` | yes | [Datasources](datasources.md) | A list; `[]` is enough. |
| `llm` | yes | [LLMs](llm.md) | The model every agent uses, and per-agent overrides. |
| `embeddings` | yes | [Embeddings](embeddings.md) | `{provider: local}` is a complete section. |
| `policies` | yes | [Policies](policies.md) | `{allow_all: true}` is a complete section. |
| `storage` | yes | [Storage](storage.md) | `{path: .nl2sql}` is a complete section. |
| `curated` | no | [Curated Answers](curated.md) | Present means curated answers are on. |
| `secrets` | no | [Secrets](secrets.md) | The `env` provider is always there without it. |
| `settings` | no | [Settings](settings.md) | Tuning knobs; every field has a default. |
| `api` | no | [REST API](api.md) | `nl2sql-api`'s own settings; the engine ignores them. |
| `observers` | no | [below](#observers-hooks-and-evals) | A list. Absent means the default observers, `log` and `audit`; `[]` means none. |
| `hooks` | no | [below](#observers-hooks-and-evals) | A list, run in its order. |
| `evals` | no | [below](#observers-hooks-and-evals) | The eval suites, scorers and regression gate. |
| `knowledge` | no | [below](#knowledge) | A list of knowledge sources, in precedence order. Absent (or `[]`) means no knowledge. |
| `conversations` | no | [below](#conversations-chat-tools-dashboards-and-mcp) | How a model-led turn is bounded and kept. Every field has a default. |
| `chat_tools` | no | [below](#conversations-chat-tools-dashboards-and-mcp) | A list of chat tool names. Absent means `chart`, `diagram` and `report`, if registered; `[]` means none (`ask` stays). |
| `dashboards` | no | [below](#conversations-chat-tools-dashboards-and-mcp) | The starter dashboard's size and the refresh limits. Every field has a default. |
| `answers` | no | [below](#conversations-chat-tools-dashboards-and-mcp) | Saved answers: how many one person may keep. Every field has a default. |
| `mcp` | no | [below](#conversations-chat-tools-dashboards-and-mcp) | `nl2sql mcp --http`: where it listens and how it authenticates. Every field has a default. |

From Python:

```python
from nl2sql import NL2SQL, load_config

engine = NL2SQL.from_config("nl2sql.yaml")        # path=None: NL2SQL_CONFIG, then ./nl2sql.yaml
cfg = load_config("nl2sql.yaml")                  # the Config, to change in code first
engine = NL2SQL(cfg.model_copy(update={"settings": cfg.settings.model_copy(update={"tenant_id": "acme"})}))
```

See [Public Facade → Creating an engine](../api/core/public-facade.md#creating-an-engine).

### `file:` includes

| Rule | Detail |
| --- | --- |
| Shape | A section is either inline, or exactly `{file: <path>}` with no other keys. |
| Relative paths | The path is relative to `nl2sql.yaml`, not to the working directory. |
| One level | Only `nl2sql.yaml` includes. An included file holds the section's body and includes nothing itself. |
| No `version` | Only `nl2sql.yaml` has `version:`. |
| Datasource folder | `datasources: {file: datasources/}` names a folder. Every `*.yaml` in it, sorted by name and not recursive, holds one datasource. `id` may be left out and defaults to the file name; if it is given, it must equal the file name. |
| Datasource file | `datasources: {file: datasources.yaml}` names a file whose body is the list. |
| List sections | `observers`, `hooks`, `knowledge` and `chat_tools` are lists too: their file's body is the list. |
| Literal paths | `file:` values are never interpolated. |
| Each file once | Including the same file twice is an error. |

Other relative paths are relative to `nl2sql.yaml` too: `storage.path`, and a
SQLite or DuckDB `database`. The engine can be run from any working directory.

### Secret and variable references

`${env:SALES_DB_PASSWORD}` reads an environment variable; `${vault:apps/sales:password}`
reads a secret from the provider whose `id` is `vault` (see
[Secrets](secrets.md)). `env` is built in.

| Rule | Detail |
| --- | --- |
| Whole value only | A reference is the entire string. `"postgres://${env:U}@db"` is an error, and there is no default syntax. |
| Secrets resolve late | Every `api_key`, the secret providers' credentials and every value under a datasource's `connection` keep the reference. It is resolved when the component is built, so `engine.config`, logs and errors never hold a plaintext secret. |
| Everything else resolves at load | Any other field, for example `storage.path: "${env:NL2SQL_DATA_DIR}"`, is resolved by `load_config`. A variable that is not set is an error naming the field. |
| Inside `secrets:` | Only `${env:...}`: one provider cannot depend on another. |

### Observers, hooks and evals

Three sections added in release 0.7.
They are accepted and validated now; the engine runs the hooks and the
observers, and reads evals from the change named below. The observers
are documented on their own page, [Observers](observers.md).

```yaml
# observers.yaml: where the engine's events go
- type: log
  level: INFO
- type: audit
  max_age_days: 90
- type: otel                      # needs nl2sql-engine[otel]
  endpoint: http://collector:4317
- type: corp_siem                 # a plugin; its config_model checks the rest
  url: "${env:SIEM_URL}"

# hooks.yaml: code that sees every run at fixed points, in this order (see Hooks)
- type: corp_sql_review
  points: [execute.before]        # a subset of the hook's own points; all of them by default
  on_error: closed                # closed (the default) | open
  approver_roles: [dba]           # who may approve its needs_approval; default policies.admin_roles
  url: "${env:REVIEW_URL}"        # the hook's own keys

# evals.yaml: the suites engine.evals.run runs, and how they are scored
suites: [evals/chinook.yaml]      # relative to nl2sql.yaml
scorers: [result_match, outcome, faithfulness, retrieval_recall, routing]   # the default: all five
curated: true
max_cost_usd: 5.00                # across a run in mode full; required for it
gate:
  max_regressions: 2
  alpha: 0.05
  max_cost_increase: 0.20
```

| Section | Item | Rule |
| --- | --- | --- |
| `observers` | `{type: <name>, ...}` (`ObserverConfig`) or an observer object in code | `type` names a registered observer (`nl2sql.observers`); its `config_model` checks the other keys, and keeps their `${...}` references until it is built. Without the section the engine runs `log` and `audit`; `observers: []` runs none. |
| `hooks` | `{type: <name>, points, on_error, approver_roles, ...}` (`HookConfig`) or a hook object in code | `points` takes `run.before`, `datasources.after`, `plan.after`, `execute.before`, `execute.after` and `answer.after`; any other is an error. `on_error` is `closed` or `open`. The hook's `config_model` checks its own keys. A point the hook does not declare is an error. See [Hooks](hooks.md). |
| `evals` | `EvalsConfig` | Strict keys. `suites` are files of cases, read when a run asks for them, not when the engine starts. `gate.alpha` is between 0 and 1. See [Evals](../evals/index.md). |

```python
from nl2sql import Config, HookConfig, ObserverConfig

Config(..., observers=[ObserverConfig(type="log"), MyObserver()],
       hooks=[HookConfig(type="corp_sql_review", points=["execute.before"]), MyHook()])
```

### Knowledge

The knowledge sources the glossary comes from, in precedence order (release
0.8). The engine loads them when it is
built and on `engine.knowledge.reload()`; see [Knowledge](knowledge.md) for
the glossary, precedence and who sees what.

```yaml
# knowledge.yaml: a list, in precedence order
- type: yaml
  path: glossary.yaml             # relative to nl2sql.yaml
- type: corp_catalog              # a plugin; its config_model checks the rest
  url: "${env:CATALOG_URL}"
```

| Section | Item | Rule |
| --- | --- | --- |
| `knowledge` | `{type: <name>, ...}` (`KnowledgeSourceConfig`) or a knowledge source object in code (one with `load`) | `type` names a registered knowledge source (`nl2sql.knowledge_sources`); its `config_model` checks the other keys and keeps their `${...}` references until it is built. A source only reads; `glossary.yaml` itself is data, not a config section. |

```python
from nl2sql import Config, KnowledgeSourceConfig

Config(..., knowledge=[KnowledgeSourceConfig(type="yaml", path="glossary.yaml"), MySource()])
```

### Conversations, chat tools, dashboards and MCP

Four sections for release 0.9. They are
accepted and validated now; the change named with each reads it. Each
is optional, and absent, the defaults shown apply. Chart limits are
`settings.charts` ([Settings](settings.md#charts)).

```yaml
# conversations.yaml (every value shown is the default)
context_turns: 3        # turns the model sees
max_tool_calls: 12      # tool calls per turn; the model then makes one last call, tools off
max_turns: 50           # the 51st turn is refused
max_age_days: 30        # null keeps conversations; also bounds draft dashboards

# chat_tools.yaml: a list of registered names (the nl2sql.chat_tools entry point group)
# absent: chart, diagram, report, dashboard_edit and dashboard_pin (those the registry has)
- chart
- report
- dashboard_edit
- dashboard_pin

# dashboards.yaml (every value shown is the default)
max_tiles: 10                        # the starter dashboard's size
min_refresh_minutes: 15              # a schedule below this is refused
schedule_identity_max_age_days: 30   # a schedule stops when its saved identity is older

# answers (every value shown is the default)
max_per_user: 100                    # saved answers one person may keep; the next save is refused

# mcp.yaml (every value shown is the default)
http:
  host: 127.0.0.1
  port: 8765
  path: /mcp
  allowed_hosts: ["127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1", "localhost", "[::1]"]   # Host headers accepted
  allowed_origins: []                              # browser Origins accepted
auth: api_keys            # api_keys | none (none: loopback only)
```

| Section | Rule |
| --- | --- |
| `conversations` | `Conversations`: strict keys; every count is at least 1, and `max_age_days: null` keeps conversations. Read by `engine.conversations`. The `conversation` agent that leads a turn is one of the [LLM agents](llm.md#per-node-models). |
| `chat_tools` | A list of names: none twice, each a registered chat tool (`registry.add_chat_tool`, or an entry point). An unknown name is an error when the engine is built (`chat_tools[i]` in the report), with the closest registered name; `ask` and a plugin whose `args_model` does not forbid extra keys are errors too. Absent means the default tools (`chart`, `diagram` and `report`) that the registry has, so an engine on `Registry.empty()` starts with none and needs no `chat_tools: []`; a name you list must be registered. `[]` means none; `ask` is the conversation's own tool and is always on. Read by `engine.conversations` and `engine.chat_tools`; see [Chat tools](../conversations/tools.md). |
| `dashboards` | `Dashboards`: strict keys; every value is at least 1. Read by `engine.dashboards`. |
| `answers` | `Answers`: strict keys; `max_per_user` is at least 1. Read by `engine.answers`: the save past the cap is refused with `AnswerLimitReached`, never by dropping an older answer. |
| `mcp` | `Mcp`: strict keys. `http.port` is 1 to 65535 and `http.path` starts with `/`. `auth: none` is for loopback only: the server refuses to start on any other host, or when `allowed_hosts` lists a name that is not loopback. `allowed_hosts` and `allowed_origins` feed the MCP SDK's DNS-rebinding protection: a request whose `Host` or `Origin` is not listed is refused, so a deployment behind a proxy lists its public host name. Read by `nl2sql mcp --http`; see [MCP over HTTP](../get-started/mcp.md#over-http). |

```python
from nl2sql import Config
from nl2sql.config import Conversations

Config(..., conversations=Conversations(context_turns=5), chat_tools=["chart", "report"])
```

### Mode

```yaml
mode: your_data    # demo | your_data | hosted (default your_data)
```

What the project is for: the bundled `demo`, your own data, or a `hosted` public demo. It is a top-level key, not a section. The engine only validates it. `nl2sql.resolve_mode(flag, env, file)` picks the mode a process runs in: the launcher flag, else the value of `NL2SQL_MODE`, else this key. It is a pure function; the entry point reads the environment and passes the value in: `nl2sql-api serve` is the one place that reads `NL2SQL_MODE`, resolves it with the `--demo` and `--hosted` flags and the file, and builds the engine with the result in its `Config`. An unknown value is an error naming the three.

### Strict keys and errors

Every section forbids unknown keys and suggests the closest known one. Names
resolved through the [registry](../extend/index.md) (`type:`,
`provider:`) suggest from the installed names. `load_config` and `NL2SQL(...)`
collect every problem before raising one `ConfigError`, each naming the file
it is in (an included file, not `nl2sql.yaml`):

```text
nl2sql.yaml has 2 problems:

  datasources/sales.yaml → preferd_tables
    Unknown key. Did you mean 'preferred_tables'?

  llm.yaml → provider
    'antropic' is not an installed LLM provider. Did you mean 'anthropic'?
```

A secret is shown as its reference, never as its value.

## Environment variables

Only these are read directly:

| Variable | Read by | Purpose |
| --- | --- | --- |
| `NL2SQL_CONFIG` | `load_config()` with no path, the CLI, `nl2sql-api` | Path to `nl2sql.yaml`. Without it, `./nl2sql.yaml`. |
| `NL2SQL_MODE` | `nl2sql-api serve` | `demo`, `your_data` or `hosted`: the mode when no flag sets it. It beats the config's `mode:` and loses to `--demo` and `--hosted`. |
| `NL2SQL_LOG_LEVEL` | `configure_logging()` | The log level when the caller passes none (`DEBUG`, `INFO`, ...). Without it, `INFO`. |
| `NL2SQL_LOG_FORMAT` | `configure_logging()` | `text` or `json` (one JSON object per line, with its `trace_id`) when the caller chooses neither; the CLI's `--log-format` chooses for any command. Without it, `text`; any other value is an error (`ValueError`; the CLI prints it on one line and exits 1). |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY` | `nl2sql/llm/` | The fallback key when `llm.api_key` (or an agent's, or `embeddings.api_key`) is not set. |

Anything else from the environment is written in the YAML as `${env:VAR}`.
There are no generic overrides such as `NL2SQL__SECTION__KEY`. Third-party
libraries (the OpenTelemetry SDK) still read their own standard variables, the
trace redactor scans the environment for values to mask. Nothing in nl2sql writes the environment: a key the
demo is given travels in the engine's LLM config, and only the `.env` loading
below adds variables. The architecture tests enforce both
([invariants](../how-it-works/invariants.md#an-engine-owns-its-config-no-process-globals)).

### `.env` files

Only the entry points load a `.env` file: the CLI, `nl2sql-api` and
`nl2sql demo` load the `.env` **next to `nl2sql.yaml`** into the process
environment at startup. A variable that is already set always wins; the file
never overrides it. Each loads the `.env` before it configures logging, so a
`NL2SQL_LOG_LEVEL` written there applies (`nl2sql-api serve --demo` reads the demo
project's `.env`, except with `--hosted`, which reads none).

The library itself never reads a `.env` file. A Python caller that wants one
loads it before building the engine:

```python
from dotenv import load_dotenv
from nl2sql import NL2SQL

load_dotenv("my-project/.env")   # or nl2sql.load_env_file("my-project/nl2sql.yaml")
engine = NL2SQL.from_config("my-project/nl2sql.yaml")
```

`nl2sql init` adds `.env` to `.gitignore`. Keep keys there, or in the
environment, never in the YAML.

## The CLI

Every command reads the same config, named once, before the subcommand:

```bash
nl2sql --config prod/nl2sql.yaml index
nl2sql --config prod/nl2sql.yaml run "Top 5 customers by revenue"
NL2SQL_CONFIG=prod/nl2sql.yaml nl2sql curated validate
nl2sql run "..."                       # ./nl2sql.yaml
nl2sql --log-format json run "..."     # JSON log lines, for any command
```

There are no per-command path flags. Commands that query or index build the
engine and call `engine.check()` first (see [Startup](#startup-check-and-required)).
`nl2sql cache`, `nl2sql feedback` and `nl2sql trace show` only need the store
paths under `storage.path`, and build no engine.

## Checking a config: `nl2sql config check`

`nl2sql config check` checks the whole config without starting the engine,
so it fits a CI job or a pre-deploy step:

```bash
nl2sql --config prod/nl2sql.yaml config check            # files, names, drivers
nl2sql --config prod/nl2sql.yaml config check --connect  # and connect to every datasource
```

It does what building the engine does, short of the stores:

- reads `nl2sql.yaml` and every file it includes, and resolves `${env:...}`;
- loads every name the config uses through the registry (`type:`,
  `provider:`, the secret providers): the name is installed, implements the
  interface version this engine needs, and its drivers are installed;
- builds the components, which resolves each datasource's `connection`
  references, without connecting or calling a model.

With `--connect` it also connects to every datasource, as
[`engine.check()`](#startup-check-and-required) does, and a datasource that
fails is a problem (`required` or not).

Problems are printed by the file they are in, then a line per secret:

```text
datasources/sales.yaml
  type
    Unknown adapter 'postgress'. Did you mean 'postgres'? Registered: duckdb, mssql, mysql, postgres, sqlite.

llm.yaml
  provider
    Unknown LLM provider 'antropic'. Did you mean 'anthropic'? Registered: anthropic, ollama, openai, openrouter.

Secrets:
  ${env:SALES_DB_PASSWORD} missing (datasources/sales.yaml → connection.password)

prod/nl2sql.yaml: 2 problems in 2 files.
```

A secret is reported as present or missing, never printed. Where the `llm`
section names no `api_key`, the provider's standard variable is listed. A
missing LLM key is reported but is not a problem, as for the engine: a CI job
rarely holds the production key, and `engine.check()` reports it at startup.
Any other secret that does not resolve stops the engine from building, so it
is a problem.

| Exit code | Meaning |
| --- | --- |
| 0 | No problems. |
| 1 | One or more problems, or the config was not found. |
| 2 | A usage error: an unknown option. |

The check creates no file under `storage.path`. From Python, the same check is
`nl2sql.config.check.check_config(path, registry=..., connect=...)`, which
returns the problems, the secrets and the datasource statuses.

## Startup: `check()` and `required`

Building the engine never connects to a datasource or calls a model: every
problem it can find without a connection is one `ConfigError`. `engine.check()`
then connects to each datasource, checks that every agent's API key resolves
(no model call) and reports the index status.

The CLI, `nl2sql-api` (in its lifespan, before it accepts requests) and
`nl2sql demo` call `check()` at startup:

- A datasource with `required: true` that fails, or datasources configured and
  none ready, raises `EngineNotReady`: the CLI and `nl2sql demo` print the report
  and refuse to start (the CLI exits 1). `nl2sql-api` logs the report and starts
  anyway: `GET /api/v1/ready` answers 503 `not_ready`, and an admin reads each
  failing datasource and its stage under `problems`.
- An optional datasource that fails is printed, left out of routing, and the
  entry point runs degraded.

```yaml
# datasources/sales.yaml
type: postgres
required: true           # without it, nothing works: the CLI refuses to start, /ready says not_ready
connection: { host: db.internal, database: sales, user: nl2sql, password: "${env:SALES_DB_PASSWORD}" }
```

A Python caller decides for itself. Without `check()`, a broken datasource
fails the first query that targets it, with its reason.

## Source references

- Models and `load_config`: `packages/nl2sql/src/nl2sql/config/`
- `init_project`, `write_project`, `starter_config`, `demo_config`: `packages/nl2sql/src/nl2sql/config/project.py`
- Secrets written as references, `write_env_values`: `packages/nl2sql/src/nl2sql/config/writer.py`
- The runtime built from a `Config`: `packages/nl2sql/src/nl2sql/runtime.py`
- The CLI's `--config`: `nl2sql-cli/src/nl2sql_cli/common/engine.py`
- `nl2sql config check`: `packages/nl2sql/src/nl2sql/config/check.py`, printed by `nl2sql-cli/src/nl2sql_cli/commands/config_check.py`
