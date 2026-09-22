# nl2sql-engine

Ask a database questions in English. The model writes a typed query plan, never
SQL text; the plan is checked against the real schema and the caller's role
before any SQL is generated.

This distribution is the engine: the LangGraph pipeline, the `nl2sql` CLI with
its browser playground, and the database adapters (PostgreSQL, MySQL, SQL
Server, SQLite, DuckDB). The REST server is the separate `nl2sql-api` package.
Full README and docs: <https://github.com/nadeem4/nl2sql>.

![The nl2sql playground: the search index and schema on the left, a question with its plan and checks on the right](https://raw.githubusercontent.com/nadeem4/nl2sql/main/docs/assets/screenshots/playground-overview.png)

## How it works

- The question is checked for answerability, split into sub-queries, and each
  gets the part of the schema it needs.
- The model returns a typed plan (a Pydantic `PlanModel`), never SQL.
- The logical validator checks every table and column against the schema,
  joins against the declared foreign keys, and every table against the caller's
  role (RBAC). A refused plan never becomes SQL.
- Only a plan that passes is rendered to SQL, deterministically, with `sqlglot`,
  then executed and summarised.

## Quickstart

Requires Python 3.12 or newer.

```bash
pip install "nl2sql-engine[demo]"
nl2sql demo
```

That writes a demo project into `./nl2sql-demo` with the Chinook sample
database, indexes its schema locally (no key; the first run downloads a ~79 MB
ONNX embedding model) and opens the playground on <http://127.0.0.1:8765/>.

**Answering a question needs a model:** `--api-key`, or `OPENAI_API_KEY`,
`OPENROUTER_API_KEY` or `ANTHROPIC_API_KEY` in the environment (Claude needs
`pip install "nl2sql-engine[demo,anthropic]"`), a key pasted into the
playground's Settings panel, or a reachable Ollama. Without one the demo runs
in replay mode, which has no recorded answers out of the box, so it can show the
schema and the index but answers nothing. `nl2sql demo --record` (with an
OpenAI or OpenRouter key) records the guided questions for later key-free runs.

The playground shows each answer's plan, validation checks, SQL, rows and cost,
a per-node Debug view, a Retrieval inspector over the live index, and a
right/wrong rating per answer.

## CLI

From the demo folder (`cd nl2sql-demo`):

```bash
nl2sql --env demo run "How many customers do we have, by country?"
nl2sql --env demo run --role viewer "Who are the top 5 customers by total spend?"   # refused
nl2sql --env demo run --no-exec "Which artist has the most albums?"                 # plan only
nl2sql --env demo index                       # re-index the schema
nl2sql --env demo doctor                      # check drivers, connectivity, key and index
nl2sql --env demo benchmark --tier 1          # gold plans, no key
nl2sql --env demo benchmark --tier 2 --model gpt-5.4 --max-cost 5
nl2sql --env demo benchmark retrieval         # retrieval recall, no key
nl2sql --env demo feedback stats              # ratings and guardrail rates
```

Every command: [CLI reference](https://github.com/nadeem4/nl2sql#cli-reference).

## Python

```python
from nl2sql import NL2SQL, UserContext

engine = NL2SQL(env="demo")          # loads .env.demo from the working directory
result = engine.run_query(
    "How many customers are there?",
    user_context=UserContext(roles=["admin"]),
)
for sq in result.sub_queries:
    print(sq.sql, [c.name for c in sq.validation if c.passed])
print(result.final_answer["summary"] if result.final_answer else result.errors)
```

Pass a `user_context` with a role: with none, `run_query` currently raises a
validation error, and an unknown role is refused.

## Install extras

```bash
pip install "nl2sql-engine[postgres]"        # or [mysql], [mssql], [duckdb]
pip install "nl2sql-engine[all]"             # every database driver; not [demo] or [anthropic]
```

SQLite needs no extra.

## Security

- The LLM provider receives the question, the schema the planner needs, sample
  column values of tables the role may read, and the result rows (for the
  written answer).
- RBAC is a per-role table allowlist; a plan touching a forbidden table is
  refused. No column masking or row-level security.
- Read-only is not enforced by the executor: only SELECTs are generated, but
  connections are not opened read-only. Give the engine a read-only database
  user.
- No authentication: the caller supplies the role.

## Status

0.1.x. `nl2sql-adapter-sdk`, `nl2sql-engine` and `nl2sql-api` share one version
and are released together. Known limitations, benchmark results and the
roadmap: <https://github.com/nadeem4/nl2sql#project-status-limitations-and-roadmap>.
MIT licensed.
