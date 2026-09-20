# nl2sql-engine

Ask a database questions in English. The model writes a **typed plan**, never
SQL text; the plan is checked against the real schema and the caller's role
before any SQL is generated.

This distribution is the whole engine: the LangGraph pipeline, the `nl2sql`
CLI, and the database adapters (PostgreSQL, MySQL, SQL Server, SQLite, DuckDB).

## Try it

```bash
pip install "nl2sql-engine[demo]"
nl2sql demo
```

That scaffolds a demo project, copies in the Chinook sample database, indexes
its schema locally (no API key needed — a small ONNX embedder runs on your
machine) and opens a browser playground showing the retrieved schema, the plan,
the validation checks, the SQL and the rows, with a role selector that shows the
validator refusing a plan before any SQL is generated.

**Answering a question needs a model**: `OPENAI_API_KEY`, `OPENROUTER_API_KEY`,
or a reachable Ollama daemon. The key-free replay mode relies on recorded model
responses, and none ship yet, so without one of those the demo can show you the
schema but cannot answer.

## Install

```bash
# Engine, CLI and adapters; sqlite works out of the box
pip install nl2sql-engine

# Add the drivers for selected dialects
pip install "nl2sql-engine[mysql,mssql]"

# Every database driver. Adapters only -- this does not include
# [demo], [aws], [azure] or [hashicorp].
pip install "nl2sql-engine[all]"
```

Requires Python 3.12+.

## Use it

```python
from nl2sql import NL2SQL

engine = NL2SQL(env="demo")
result = engine.run_query("How many employees are there?")

for sq in result.sub_queries:
    print(sq.sql)
    print([c.name for c in sq.validation if c.passed])
    print(sq.rows.rows[:5] if sq.rows else "plan only")
print(result.final_answer["summary"])
```

```bash
nl2sql setup --demo --lite                 # generate demo data and configs
nl2sql --env demo index                    # index the schemas
nl2sql --env demo run "..."                # ask a question
nl2sql --env demo run --no-exec "..."      # plan and validate, touch no database
nl2sql doctor                              # diagnose the environment
```

`QueryResult` carries, per sub-query, the plan, the validation checks with
pass/fail and a reason, a capped row sample with the true total, the SQL, a
status and a retry count; and per run, an overall status and per-node timings.

## How it works

The pipeline is a compiled LangGraph: datasource resolver → decomposer → global
planner → layer router, with a SQL-agent subgraph of schema retriever → AST
planner → logical validator → generator → executor, plus a refiner loop for
retryable failures.

The safety property is the order of those last three. The model's target is a
recursive Pydantic `PlanModel`, not a SQL string. `LogicalValidatorNode`
resolves every column against the retrieved schema snapshot with
`sqlglot.optimizer.qualify`, matches joins against declared foreign keys, and
checks every table against the caller's role policy. Only a plan that passes
reaches the generator, which renders SQL with `sqlglot` from `exp.select()`. A
plan that fails never becomes SQL, and the checks come back in the result so a
UI can show which gate refused it.

`query_type` is `Literal["READ"]`, so only SELECTs can be produced. That is
structural: the executor does not inspect the SQL and connections are not opened
read-only on any dialect, so **grant the engine a read-only database user**.

## What it does not do

- **No authentication.** The role is supplied by the caller (`--role`, or
  `user_context` on the REST API). Put your own auth in front of anything you
  expose and derive the role from it.
- **No process sandbox.** The graph runs in-process on a one-worker thread pool
  per run; a driver-level crash takes the process with it. `GLOBAL_TIMEOUT_SEC`
  bounds how long the *caller* waits, not how long the work runs.
- **No distributed tracing.** OpenTelemetry *metrics* (node duration, token
  usage) exist behind `OBSERVABILITY_EXPORTER`, which defaults to `none`. No
  spans are started; there is no Jaeger or Prometheus exporter.
- **No row-level security or column masking.** RBAC is a per-role allowlist of
  datasources and `datasource.table` strings.
- **`max_bytes` is not enforced** — it is configured and reported only.
  `row_limit` is enforced, in the generated SQL.

## Public API

`NL2SQL` is the facade. Modular sub-APIs hang off it: `engine.query`,
`engine.datasource`, `engine.llm`, `engine.indexing`, `engine.settings`,
`engine.results`, `engine.policy`, `engine.benchmark`.

Exported types: `NL2SQL`, `QueryResult`, `UserContext`, `ErrorSeverity`,
`ErrorCode`, `PipelineError`, `BenchmarkConfig`, and the modular API classes.

## Versioning

`nl2sql-adapter-sdk`, `nl2sql-engine` and `nl2sql-api` share one version number
and are released together, pinned to each other with `~=0.1`.

## Documentation

Full documentation, including the known limitations of each subsystem, is in the
repository at <https://github.com/nadeem4/nl2sql> and on the published MkDocs
site.
