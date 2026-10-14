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

## Configuration prerequisites

All paths require configuration files. Start with the example configs:

- `configs/datasources.example.yaml` → copy to `configs/datasources.yaml`
- `configs/policies.example.json` → copy to `configs/policies.json`

`configs/llm.yaml` and `configs/secrets.yaml` are tracked in the repository as
examples: an OpenAI default agent, and a secrets file whose providers are all
commented out. Edit them against the schemas in [LLMs](../configuration/llm.md)
and [Secrets](../configuration/secrets.md), or let `nl2sql setup` generate your
own.

See `configuration/system.md` for environment variables and defaults.

## Start a config with `nl2sql init` (0.5)

!!! note "0.5"
    `nl2sql init` writes the 0.5 config, `nl2sql.yaml`. The engine reads it
    from release 0.5 on (see
    [Configuration v2](../architecture/configuration_v2.md)); until then the
    files above are what it reads.

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
data/                  # --demo only: the three sample SQLite files
.gitignore             # .nl2sql/ and .env are added to it
```

- **No key is written.** `llm.yaml` names no `api_key`, so the provider's
  standard variable (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
  `OPENROUTER_API_KEY`) is read. To read another variable, write
  `api_key: "${env:MY_KEY}"`.
- **Nothing is overwritten** unless you pass `--force`. If any file `init`
  would write already exists, it names the files and writes nothing.
  `.gitignore` is never replaced; the lines it lacks are added.
- **The result is checked.** `init` loads what it wrote with `load_config`
  before it reports success, then prints the next steps.
- **`--provider`** is `openai` (the default), `anthropic` or `openrouter`.

From Python, `nl2sql.config.init_project(folder, demo=..., provider=..., force=...)`
writes the same files, and `nl2sql.config.demo_config()` builds the demo's
`Config` in code, with the databases opened read-only from the installed
package.
