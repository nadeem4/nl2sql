# CLI reference

Global options: `--config PATH` names the `nl2sql.yaml` every command reads
(without it, `NL2SQL_CONFIG`, then `./nl2sql.yaml`), and `--version` prints the
installed version. The `.env` next to that `nl2sql.yaml` is loaded first;
variables already exported in your shell win over the file. Paths inside the
config are relative to `nl2sql.yaml`, so a command works from any folder. Every
command has `--help`.

| Command | Purpose | Key options |
| --- | --- | --- |
| `nl2sql chat` | A terminal chat over your data (Textual): ask, follow up, `F2` why this answer, `F3` the SQL, `/chart`, `/report`, approve. Keyboard only | `--demo`, `--dir` (default `nl2sql-demo`), `--as ROLE`, `--user NAME`, `--datasource ID` |
| `nl2sql demo` | Start `nl2sql-api serve --demo` (it writes the demo project, indexes it and serves the web app) and open the browser | `--dir` (default `nl2sql-demo`), `--host` (default `127.0.0.1`), `--port` (default 8765), `--no-browser` |
| `nl2sql init [DIR]` | Write a new config into `DIR` (default `.`): `nl2sql.yaml` plus one file per section, checked with `load_config`. Never writes a key and never overwrites a file without `--force` | `--demo` (the three sample databases, their roles, verified queries, sample questions and settings), `--provider` (`openai`, `anthropic` or `openrouter`; default `openai`), `--force` |
| `nl2sql run "QUESTION"` | Ask a question and print the plan, checks, SQL, rows and answer | `--role` (default `admin`), `--no-exec` (plan and validate only), `--ds-id`, `--verbose`/`-v`, `--show-perf` |
| `nl2sql index` | Index datasource schemas into the index of the configured embedding model, one datasource at a time | `--datasource`/`-d ID` (repeatable), `--refresh` (write the entries again from the stored snapshot, the glossary and the `curated` section after a change; no schema read, no model call, and only changed entries are embedded), `--check` (re-index nothing: compare each live database with the schema its index was built from; exits 0 unchanged, 1 drifted, 2 a check failed or nothing was checked; with `--role` (default `admin`) and `--json`) |
| `nl2sql mcp` | Serve the engine to an MCP client: `ask`, `list_datasources`, `describe_table`. Over stdio by default, as the role given (refuses to start with no role and no `policies.default_role`); with `--http`, over HTTP behind bearer API keys, each running as its key's roles. Needs `pip install "nl2sql-engine[mcp]"` | `--role` (repeatable), `--user` (default `mcp`), `--http`, `--host`, `--port` |
| `nl2sql index prune` | Drop the index of every other embedding model (and 0.5's collection); the current one is kept | |
| `nl2sql config check` | Check the whole config without starting the engine: every file, include and `${env:...}`, and every component it names (installed, right interface version, drivers present). Problems are printed by file; each secret is reported present or missing, never printed. Exits 0, or 1 on any problem. Writes nothing under `storage.path` | `--connect` (also connect to every datasource; one that fails is a problem) |
| `nl2sql doctor` | Check Python, installed drivers and provider keys, then build the configured engine and print `engine.check()`: each datasource, each agent's key, the index | |
| `nl2sql list-adapters` | List the installed datasource adapters | |
| `nl2sql install NAME` | pip-install the driver extra for an adapter (`postgres`, `mysql`, `mssql`, `duckdb`, `sqlite`: the `connection.type` names), after a confirmation | |
| `nl2sql policy validate` | Check the config's policies against its datasources | |
| `nl2sql curated validate` | Check the `curated` section (verified queries): ids, params, read-only SQL in each datasource's dialect, tables against the index; exits 1 on any error | |
| `nl2sql curated test` | Run every verified query once against its database, read-only, with each param's default or example bound; reports pass/fail per query and exits 1 on any failure | |
| `nl2sql knowledge show` | The glossary as the engine uses it, each item with its origin (`knowledge`, `database`, `derived`); what the role cannot read is left out; problems last, exit 1 when there are any | `--datasource ID`, `--as ROLE` (repeatable), `--user` |
| `nl2sql knowledge export` | The glossary items the role may see as one canonical `glossary.yaml`; never over a config or source file | `--datasource ID` (repeatable), `--out FILE` (default: stdout), `--as ROLE`, `--user` |
| `nl2sql knowledge sources` | Each knowledge source: type, item counts, hash, when it loaded, its last reload error | `--as ROLE`, `--user` |
| `nl2sql review list` | The review queue: proposed knowledge and verified queries the role may see, newest first | `--kind K`, `--datasource ID`, `--status S` (default `proposed`; `all`), `--as ROLE` (repeatable), `--user` |
| `nl2sql review show ID` | One proposal, with the current glossary or curated value beside the proposed change | `--as ROLE`, `--user` |
| `nl2sql review accept ID...` | Accept proposals as an admin role: writes the glossary and `curated.yaml` once each, then reloads and refreshes; exits 1 when an item is refused | `--all` (with `--kind`, `--datasource`), `--as ROLE`, `--user` |
| `nl2sql review reject ID...` | Reject proposals as an admin role; a rejected change is never proposed again | `--reason TEXT`, `--as ROLE`, `--user` |
| `nl2sql review export` | The open proposals the role may see as a review file, to decide in a pull request | `--out FILE`, `--kind K`, `--datasource ID`, `--as ROLE`, `--user` |
| `nl2sql review apply FILE` | Read a review file as an admin role: accept each `accept` (with its edited change), reject each `reject` | `--as ROLE`, `--user` |
| `nl2sql profile` | Profile the indexed schema and file what it finds for review, and with `--draft` have the model draft missing descriptions; arrives in 0.8 | `--datasource ID`, `--draft`, `--max-cost USD`, `--as ROLE`, `--user` |
| `nl2sql launch` | Find a database (a file, `DATABASE_URL` or a local port), test it, write the project with secrets in `.env`, index, profile, file five starter questions and review what it found, in one sitting; also `uvx --from nl2sql-engine nl2sql launch`; arrives in 0.9 | `DIR`, `--provider`, `--as ROLE`, `--draft`, `--yes` (accept nothing; write `review.yaml`), `--no-ports` |
| `nl2sql keys create` | Create an API key for MCP over HTTP; the token is printed once; managing keys needs an admin role (under `allow_all` leave out `--as` and `--role`) | `--user`, `--role` (repeatable), `--tenant`, `--label`, `--as ROLE` |
| `nl2sql keys list` | The API keys, never a secret (under `allow_all` leave out `--as`) | `--as ROLE` |
| `nl2sql keys revoke ID` | Revoke an API key; it stops verifying at once (under `allow_all` leave out `--as`) | `--as ROLE` |
| `nl2sql dashboards refresh` | Re-run the stored plans of a dashboard, or of every dashboard that is due, with no model call; arrives in 0.9 | `--dashboard ID`, `--due`, `--as ROLE` |
| `nl2sql trace show TARGET` | Print a run trace's timeline: nodes, attempts, durations, tokens, errors | a trace file, or a trace id in the trace store (`storage.traces`) |
| `nl2sql trace replay TARGET` | Re-run a traced question on its recorded LLM responses, with no model calls | |
| `nl2sql cache clear` | Empty the plan cache in `<storage.path>/state.db`; schema snapshots are kept | |
| `nl2sql storage purge` | Delete traces and results older than their store's `max_age_days` (14 and 7 days by default), and decided proposals older than `settings.review.max_age_days` (365); `nl2sql-api` does this at startup and once a day | |
| `nl2sql feedback list` | Playground ratings, newest first | `--limit` (default 50) |
| `nl2sql feedback stats` | Ratings plus guardrail rates: refusals, refiner retries, validator rejections, errors, plan-cache hits | `--json`, `--traces DIR` |
| `nl2sql feedback export --good` | Write thumbs-up runs as draft eval cases to a separate suite file for review | `--out` (default `feedback_gold_drafts.yaml`) |
| `nl2sql feedback clear` | Delete every rating | `--yes` |
| `nl2sql feedback correct TRACE_ID` | Rate a run down with its fix; the fix is checked as the role (validator or read-only and scope, `execute.before` hooks, one run) and filed in the review queue as a verified query | `--sql FILE` or `--plan FILE`, `--question TEXT`, `--note TEXT`, `--as ROLE`, `--user` |
| `nl2sql eval run` | Run the eval suites in one mode (`full`: the model, priced and capped; `plans`: the gold plans on a fake LLM; `retrieval`: no model) and, with `--baseline`, gate the run against a report, a record or a `benchmarks/` folder. Exits 1 on a regression, 2 on a usage error or a baseline it cannot compare, 3 at the cost cap; mode full refuses to run when `CI` is set | `--mode`, `--suite`, `--llm` (preset, file or `NAME=`), `--cases`, `--passes`, `--max-cost`, `--baseline`, `--out` (default `eval_<mode>.json` next to `nl2sql.yaml`) |
| `nl2sql eval regenerate PATH` | Write a suite's expected rows: each approved case's SQL through its datasource's adapter; exits 1 when a case's SQL failed | |
| `nl2sql eval seed` | Draft eval cases from a datasource's schema, as a role: the model writes questions with typed plans, the engine validates each, passes it through the `execute` hooks and runs it read-only, and the survivors are appended as `status: draft` for review. Exits 2 on a refusal, 3 at the cost cap or the call limit; refuses to run when `CI` is set | `--datasource`, `--out`, `--count` (default 20), `--role` (default `policies.default_role`), `--max-cost` (default `evals.yaml`'s) |
| `nl2sql eval publish [REPORT ...]` | Record each report `eval run` wrote under `benchmarks/`, then rebuild `docs/benchmarks.md` and the results block above; run from the repo root | `--note`, `--from DIR` |

The `nl2sql eval` group is the whole command; `nl2sql eval --help` lists its
subcommands. Evals also run from Python, with `engine.evals`
([Evals](../evals/index.md)).

Examples, from the demo folder (`cd nl2sql-demo`, so `./nl2sql.yaml` is the
config; from anywhere else, add `--config nl2sql-demo/nl2sql.yaml`):

```bash
nl2sql run "How many customers do we have, by country?"
nl2sql run --role viewer "Who are the top 5 customers by total spend?"   # refused
nl2sql run --no-exec "Which artist has the most albums?"                 # plan only
nl2sql feedback stats
```

`run` and `index` call `engine.check()` before they start: a
datasource marked `required: true` that cannot connect stops them with the
report (exit 1); any other datasource that fails is printed and left out.

`run` exits 1 when the run ends with an ERROR or CRITICAL error (a refusal
included). More: [Demo guide](../get-started/demo.md),
[Debugging a Run](../observability/debugging.md),
[Feedback and Signals](../observability/feedback.md),
[Evaluation](../evals/chinook-suite.md).
