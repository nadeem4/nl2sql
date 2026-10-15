# Getting Started

NL2SQL supports three ways to get started. Choose the guide that matches how you want
to use the platform:

- **PyPI (Python API)**: Install `nl2sql-engine` and use it programmatically.
- **Docker (REST API)**: Run the API service and integrate over HTTP.
- **From Source (Development)**: Clone the repo for local development and contributions.

## Choose your path

- [PyPI (Python API)](pypi.md)
- [Docker (REST API)](docker.md)
- [From Source (Development)](source.md)
- [Demo Data (CLI-first)](demo.md)
- [MCP server](mcp.md): serve the engine to a desktop assistant or an IDE with `nl2sql mcp`

## One config: `nl2sql.yaml`

Every path reads the same config, `nl2sql.yaml`, and the files it includes. The
CLI takes it from `--config PATH`, then the `NL2SQL_CONFIG` variable, then
`./nl2sql.yaml`; `nl2sql-api` from `NL2SQL_CONFIG`, then `./nl2sql.yaml`; Python
from `NL2SQL.from_config(path)`. The sections, their fields and every 0.4 file
and variable they replace are in [Configuration](../configuration/system.md).

## Start a config with `nl2sql init`

```bash
nl2sql init my-project                       # writes my-project/nl2sql.yaml and its files
nl2sql init my-project --provider anthropic  # llm.yaml names Anthropic and its default model
nl2sql init my-demo --demo                   # the demo: three sample databases, roles, verified queries
```

`init` asks no questions. It writes `nl2sql.yaml` as a table of contents and
each section in its own file:

```text
nl2sql.yaml            # the table of contents
datasources/
  example.yaml         # a sqlite example     (--demo: chinook.yaml, support.yaml, webanalytics.yaml)
llm.yaml               # provider and model; no key
embeddings.yaml        # provider: local
policies.yaml          # allow_all: true      (--demo: admin, analyst and viewer roles)
storage.yaml           # path: .nl2sql
curated.yaml           # --demo only: the demo's verified queries
settings.yaml          # --demo only: a 300 s timeout and a trace for every run
data/                  # --demo only: the three sample SQLite files
.gitignore             # .nl2sql/ and .env are added to it
```

- **No key is written.** `llm.yaml` names no `api_key`, so the provider's
  standard variable (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
  `OPENROUTER_API_KEY`) is read. Put it in your environment or in a `.env`
  file next to `nl2sql.yaml`: the CLI, `nl2sql-api` and `nl2sql demo` load that
  file at startup and never let it override a variable that is already set. To
  read another variable, write `api_key: "${env:MY_KEY}"`.
- **Nothing is overwritten** unless you pass `--force`. If any file `init`
  would write already exists, it names the files and writes nothing.
  `.gitignore` is never replaced; the lines it lacks are added.
- **The result is checked.** `init` loads what it wrote with `load_config`
  before it reports success, then prints the next steps.
- **`--provider`** is `openai` (the default), `anthropic` or `openrouter`.

Then point `datasources/example.yaml` at your database (or rename it: the file
name is the datasource id), and build the index:

```bash
cd my-project
nl2sql index                                 # or, from elsewhere: nl2sql --config my-project/nl2sql.yaml index
nl2sql run "How many customers are there?"
```

`nl2sql index` and `nl2sql run` first call `engine.check()`: each datasource
connects, or is reported and left out. A datasource with `required: true` that
cannot connect stops the command. `nl2sql doctor` prints the same report
without running anything.

From Python, `nl2sql.config.init_project(folder, demo=..., provider=..., force=...)`
writes the same files, and `nl2sql.config.demo_config()` builds the demo's
`Config` in code, with the databases opened read-only from the installed
package.
