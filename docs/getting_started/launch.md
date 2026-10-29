# Launch

`nl2sql launch [DIR]` takes you from "I have a database" to a working project in one sitting. It finds the
database, asks for what is missing, tests the connection, writes the project, indexes it, profiles it and
files five starter questions, then lets you review what it found. Every step is a package call
(`engine.datasources.scan`, `test`, `save`, `engine.index.build`, `engine.knowledge.profile`,
`engine.dashboards.suggest_questions`, `engine.proposals`), so Python and REST callers can do the same.

```bash
pip install "nl2sql-engine[demo]"
nl2sql launch my-project
```

or, with nothing installed first:

```bash
uvx --from nl2sql-engine nl2sql launch my-project
```

## Options

| Option | Meaning |
|---|---|
| `DIR` | The project folder (default `.`). Without a `nl2sql.yaml` there, `launch` writes a new project; with one, it adds one datasource to it |
| `--provider NAME` | The LLM provider and its default model for a new project (`openai`, the default, `anthropic`, `openrouter`, ...). An existing project keeps its own |
| `--as ROLE` | The admin role the launch acts as (repeatable). Default: the first of `policies.admin_roles`; under `allow_all`, none is needed |
| `--draft` | Also have the profiler model draft descriptions, under `settings.profiler.max_cost_usd`. The only step that calls a model |
| `--yes` | Accept nothing: write `DIR/review.yaml` for review in git instead of asking |
| `--no-ports` | Do not look for databases on local ports |

## What happens

| Step | What you see |
|---|---|
| Find | A numbered list: database files under `DIR` (two folders down), the `DATABASE_URL` environment variable, and servers listening on their default ports on `127.0.0.1` (PostgreSQL 5432, MySQL 3306, SQL Server 1433), then "Another database". Nothing connects while it looks. |
| Fill | A prompt for each field the finding could not fill. A password is hidden. |
| Name | The datasource id, defaulting to the file's name. |
| Test | A connection test. On failure it prints the stage (`network`, `auth`, ...), the reason and the fix, and asks for the fields again. |
| Model key | The provider's key from the environment, or asked (hidden). It is not checked with a call; your first question is the check. |
| Write | A new folder gets the whole project (`nl2sql.yaml` and a file per section, `glossary.yaml`); an existing project gets `datasources/<id>.yaml`. Secrets go to `.env` and the YAML holds `${env:NL2SQL_...}` references. |
| Index | Builds the index, with progress. The first run downloads the local embedding model. |
| Profile | Joins the database does not declare and column roles, filed as proposals ([Schema Profiler](../schema/profiler.md)). |
| Starter questions | Five questions from the [starter dashboard](../dashboards/index.md)'s templates, printed and filed as `example` proposals. |
| Review | Each waiting proposal: accept, reject, skip or edit. |
| Next | The `nl2sql run` command for the first starter question, and `nl2sql demo --dir DIR` to open the playground on your project. `demo` warns about an old engine only for a folder whose demo stamp is older, so a launched folder (it has no stamp) opens without a warning. |

## Secrets

A connection password and the model key are written to `DIR/.env`, which `launch` adds to `.gitignore`, and
never to YAML. After writing, `launch` loads that `.env` and rebuilds the engine from the files it wrote, so
the key and every `${env:...}` reference resolve in the same run and the files are proven to load. A variable
already set in your environment is never overridden, and a key already in the environment is used without
being written. No password or key is printed, not in the candidates, an error or a summary.

## Review, and what `--yes` does

The profiler's findings and the starter questions wait in the [review queue](../configuration/review.md).
Nothing a model or a template drafted reaches a prompt, the index or `glossary.yaml` until an admin accepts
it. In the prompt, `a` accepts, `r` rejects, `s` (the default) leaves it for later and `e` edits its change as
JSON before accepting. An accepted item is written to `glossary.yaml`; run `nl2sql index --refresh` so it
reaches the index.

With `--yes`, nothing is accepted. `launch` exports the open proposals to `DIR/review.yaml`; set each
`decision` in a pull request and run `nl2sql review apply DIR/review.yaml --as ROLE`.

## Drivers

An adapter whose driver is not installed is listed but cannot be used. `launch` prints the two fixes and
stops: install it (`pip install "nl2sql-engine[postgres]"`), or, if you started with `uvx`, start again with
the extra included (`uvx --from "nl2sql-engine[postgres]" nl2sql launch`). It does not install anything
itself, because an install inside `uvx`'s throwaway environment would not last.

`launch` is the one command that reads `DATABASE_URL`.
