# Configuration Loading

How `nl2sql.yaml`, the files it includes and `.env` become one `Config`, and which code may touch the environment, for contributors and operators who want to know why a setting was or was not picked up.

The field-by-field reference is [Configuration System](../configuration/system.md). This page is about the loading rules.

## One file, optional includes

`load_config(path)` in `packages/nl2sql/src/nl2sql/config/loader.py` reads one `nl2sql.yaml` and returns a `Config`. The path is the argument, else `$NL2SQL_CONFIG`, else `./nl2sql.yaml`.

- A section is either written inline or is exactly `{file: <path>}`, relative to `nl2sql.yaml`. No other keys may sit beside `file`, and a `${...}` reference inside a `file:` value is an error (paths are literal).
- Only `nl2sql.yaml` includes, and only it has `version`. An included file holds the section's body and cannot include another file.
- `datasources` may name a folder instead: one datasource per `*.yaml`, with the file name as its `id`. No other section may include a folder.
- A file may be included once.
- The section files are plain YAML, so a person edits them in git. The loader records the SHA-256 of `nl2sql.yaml`, each section file and each datasource file. When the engine writes one of them (saving a datasource, adding the demo), it refuses with `FILE_CHANGED` if the file on disk no longer matches, so it never overwrites a hand edit it did not see.

## References

- `${provider:key}` must be a whole value, never part of a string.
- A secret field and a datasource's `connection` keep the reference unresolved, so the engine resolves it through the secret provider when it connects. Everything else resolves `${env:NAME}` at load time, from the process environment.
- Inside the `secrets` section only `${env:...}` is allowed.
- Data that did not come from the operator's file (a request body, for example) goes through `parse_section`, which applies the same strict rules and treats an `${env:...}` reference outside a secret field as an error instead of reading it.

## Strict keys, all problems at once

Every section is a strict model: an unknown key is an error with a "did you mean" suggestion, and a key a release renamed names its replacement. The loader collects every problem it finds and raises one `ConfigError` that lists them all, so a person fixes a file once, not one error per run.

## The library never reads `.env`

`load_config` and the `NL2SQL` facade never read a `.env` file and never write `os.environ`. A process that wants the `.env` next to `nl2sql.yaml` calls `load_env_file(path)`, which loads it with `override=False`, so a variable already set wins. The entry points that do:

- `nl2sql_cli/main.py` (every command), and the CLI's `launch` and `chat` commands for the folders they create or read;
- `nl2sql_api/serve.py`, for `nl2sql-api serve`.

Hosted mode never loads one: a hosted server must hold no model key, and `serve` refuses to start if the environment has one. If you use the engine as a library, load the environment yourself, or pass `${env:...}` references and set the variables before `load_config`.

## Where the environment is read

The environment is read only where an allow-list says (`READS_THE_ENVIRONMENT` in `packages/nl2sql/tests/architecture/test_boundaries.py`):

| Where | What |
| --- | --- |
| `nl2sql/config/` | `NL2SQL_CONFIG`, and `${env:...}` references |
| `nl2sql/llm/` | the provider key variables, from one table |
| `nl2sql/secrets/providers/env.py` | the `env` secret provider |
| `nl2sql/common/logger.py` | `NL2SQL_LOG_LEVEL`, `NL2SQL_LOG_FORMAT` |
| `nl2sql/tracing/trace.py` | the trace redactor masks every value the environment holds |
| `nl2sql_cli/commands/eval.py` | `CI`, so a `full` eval never runs in CI |
| `nl2sql_cli/commands/launch.py` | `DATABASE_URL`, the one URL `nl2sql launch` offers as a candidate |
| `nl2sql_api/serve.py` | `NL2SQL_MODE`, the mode of this process |

Anything else is configuration and belongs in `nl2sql.yaml`, as `${env:VAR}` when it should come from the environment. Nothing writes `os.environ`; the config writer (`nl2sql/config/writer.py`) writes a file and leaves the process alone.

## Mode

`mode` is `demo`, `your_data` (the default) or `hosted`. `resolve_mode(flag, env, file)` picks the first of the launcher flag, `NL2SQL_MODE` and the file's value. It is pure: the entry point reads the environment and passes the value in. The web app learns the mode from `GET /meta`.

## Why this shape

Two engines in one process must share nothing, so configuration travels in a `Config` object and not in globals. A person must be able to read, diff and review every file, so includes are plain and one level deep. The cost of strictness is paid once, at load, with every problem listed. See [Packages and boundaries](packages-and-boundaries.md) for the rule that keeps the environment out of the library.
