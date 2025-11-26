# NL2SQL Multi-Agent System Plan (LangGraph)

## Goals and Constraints
- Translate natural language into correct, efficient SQL with high reliability.
- Support schema-aware generation, safety/guardrails, and result verification.
- Prioritize debuggability and deterministic control over agent flows.
- Support heterogeneous engines (Postgres, MySQL/MariaDB, SQL Server/Azure SQL, Oracle, etc.) with engine-specific dialect handling.
- Assume access to DB connection (SQL), schema metadata, and optional docs/FAQ store.

## High-Level Architecture
- Client/API layer receives NL query and context (user, datasource).
- LangGraph orchestrates agents; state holds user input, schema, plans, SQL drafts, results, and rationale.
- Tooling layer exposes: schema introspection, vector/keyword search over docs, SQL execution (read-only by default), cost/limit guardrails, and logging/telemetry.
- Storage: short-term graph state (per run) + optional run history for learning/evals.

## Agent Roles (Nodes)
- **Supervisor**: routes through the graph based on state flags and errors.
- **Intent Analyst**: normalizes NL input, extracts entities/filters, and clarifies ambiguity (can return clarifying questions).
- **Schema Retriever**: fetches relevant tables/columns, PK/FK info, and sample rows; caches in state.
- **Query Planner**: proposes a relational plan (joins, filters, aggregates, limits) in a structured plan schema.
- **SQL Generator**: turns the plan into SQL with parameterization and LIMIT; tags with safety budget.
- **SQL Linter/Validator**: static checks (syntax via dry-run, forbidden ops, cost estimation, limit enforcement).
- **Execution Agent**: runs SQL in read-only mode, captures result samples and metrics; never executes writes (only drafts if enabled).
- **Verifier**: cross-checks results vs. intent and schema assumptions; triggers correction loop if mismatch.
- **Answer Composer**: generates the final answer/explanation and optional follow-up questions.
- **Evaluator (optional)**: offline/async scoring for regressions using golden queries or synthetic data.

## LangGraph Sketch
- State (typed dict):
  - `user_query`, `clarifications`, `entities`, `filters`, `schema`, `plan`, `sql_draft`, `validation`, `execution`, `answer`, `errors`, `retries`.
- Control flow:
  1) Start → Intent Analyst → Schema Retriever → Query Planner → SQL Generator.
  2) SQL Linter/Validator. If fail or risky → back to Planner/Generator with feedback; enforce retry cap.
  3) Execution Agent (read-only, LIMIT). On runtime errors → back to Planner/Generator with error details.
  4) Verifier decides: if mismatch → correction loop; else → Answer Composer → End.
- Guardrails:
  - Hard stops on DDL/DML execution; default LIMIT; cost budget; retry limits; supervisor exit on repeated failures; optional draft-only writes if enabled.

## Tools and APIs
- `get_schema(datasource, tables_hint)`: columns, types, PK/FK, indexes, row counts; returns engine id and capabilities.
- `search_docs(query)`: semantic/keyword over business docs/FAQ.
- `sample_rows(table, limit)`: small samples for value distributions.
- `execute_sql(sql, params, mode=read-only)`: returns rows, metrics (row count, time), error codes.
- `dry_run(sql)`: syntax and estimated plan/cost if supported.
- `log_event(stage, payload)`: telemetry for tracing and evals.
- `get_engine_capabilities(engine)`: exposes dialect flags (LIMIT/TOP/FETCH, ILIKE vs LOWER, DATE functions, identifier quoting).

## Connection and Driver Strategy
- Use SQLAlchemy Core everywhere as the single abstraction; only swap engine URLs and driver packages.
- Start with SQLite for local development/tests (built-in dialect), but keep the interface pluggable for Postgres/MySQL/SQL Server/etc.
- Engine drivers (SQLAlchemy dialects):
  - SQLite: built-in
  - Postgres: `psycopg`/`psycopg2`
  - MySQL/MariaDB: `mysqlclient` or `mysql-connector-python`
  - SQL Server/Azure SQL: `pyodbc` with ODBC DSNs
- Optional analytics: `snowflake-sqlalchemy`, `pybigquery` (BigQuery), `oracledb`/`cx_Oracle` (Oracle)
- LangGraph tools call SQLAlchemy for introspection and execution; dialect flags from `get_engine_capabilities` tune rendering (LIMIT/TOP/FETCH, quoting, functions).
- Keep execution read-only by default (roles/replicas); apply per-engine session settings (statement timeout, row/bytes limit) when creating engines.

## Datasource Configuration
- Store per-datasource profiles (YAML/JSON/DB) with fields: `id`, `engine`, `sqlalchemy_url/DSN`, `auth` (user/pass or managed identity), `read_only_role`, `statement_timeout_ms`, `row_limit`, `max_bytes`, and `tags` (PII sensitivity, env). SQLite profiles can omit auth/roles.
- Allow feature flags per datasource: `allow_generate_writes` (off by default; can draft DDL/DML but never execute), `allow_cross_db`, `supports_dry_run`, `supports_estimated_cost`, `sample_rows_enabled`.
- Supervisor loads the profile at run start and injects capabilities + limits into state; Execution Agent enforces limits per profile.

## Knowledge Retrieval (Docs/FAQ)
- Use Chroma DB for semantic search over business logic docs, FAQs, and glossary; fall back to keyword search for precision terms (IDs, codes).
- Store doc metadata (datasource, table/column mentions, freshness) to filter retrievals per query.
- Enforce chunking and embedding limits; prefer OpenAI text-embedding-3-large or local alternative if network-restricted.
- LangGraph `search_docs` tool queries Chroma with filters (datasource/table) and returns top-k snippets plus citations.

## Prompting and Formats
- Use structured outputs (JSON) for planner and generator to keep joins/filters explicit.
- Canonical SQL template: CTE-first, explicit join conditions, safe LIMIT, `-- rationale` comment for traceability.
- Dialect-aware rendering layer converts the structured plan into engine-specific SQL (LIMIT vs TOP/OFFSET FETCH, string/date funcs, quoted identifiers).
- Verifier prompt compares intent, schema slice, and result sample; requests specific fixes; includes engine context for error interpretation.

## Observability
- Prefer OpenTelemetry for tracing/metrics/logs; emit OTLP to a collector and onward to your APM (Jaeger/Tempo/Datadog/etc.).
- Attach span attributes for datasource, engine, graph node, retries, cost, and safety flags; capture SQL only with redaction (no literals/PII).
- Record LangGraph run IDs in span context; keep sampling configurable (always-on in lower envs, tail-based in prod).
- Optional: dual-export LLM spans to LangSmith if you want richer prompt/result inspection alongside OTel.

## Memory and State Handling
- Per-run short-term memory in graph state; no cross-run leakage by default.
- Optional run-history store keyed by datasource and user for personalization.
- Deterministic branching: state flags (`needs_clarification`, `validation_failed`, `runtime_error`, `verified`) drive supervisor routing.

## Error Handling and Retries
- Categorize errors: validation vs. runtime vs. mismatch.
- Backoff with max retry counts per category; attach last error to state for targeted repair.
- Fallback: return safe natural-language response with partial findings and ask for refinement.

## Guardrails (defaults)
- Enforce read-only roles and parameterized SQL; block all DDL/DML execution. Optionally allow generation of write SQL for review if `allow_generate_writes` is true, but never execute it.
- Apply statement timeout (e.g., 5–15s), row limit (e.g., 500–1,000), and optional byte limit per query; kill over-budget runs.
- Require LIMIT/TOP/FETCH on generated queries; cap retries; surface risk score for large scans/joins.
- Redact literals in logs/traces; drop PII-bearing columns from samples if tagged sensitive; mask values in telemetry.
- Dry-run/estimate cost when supported; deny if estimated cost exceeds profile budget.
- Rate-limit per user/datasource; prioritize clarification if intent is ambiguous or high-risk filters (e.g., missing WHERE) are detected.

## Evaluation and Testing Plan
- Unit tests: parsers for structured planner/generator outputs; validation rules.
- Golden tests: NL → SQL pairs with schemas; assert SQL equivalence and answer correctness.
- Simulation tests: inject synthetic errors (bad join, missing filter) to verify correction loops.
- Offline evals via `Evaluator` node; store scores and traces for regression tracking.

## Incremental Delivery Milestones
1) Skeleton LangGraph with Supervisor, Intent Analyst, Schema Retriever, SQL Generator, and Validator stubs; start with SQLite fixture; dry-run only.
2) Add execution + verifier loop with retry budget; integrate read-only DB tool via SQLAlchemy engine factory.
3) Introduce planner structure + doc search tool; enforce guardrails (LIMIT/TOP/FETCH, no DDL/DML) with draft-only writes optional.
4) Add telemetry (OTel), tracing, and evaluation harness with golden cases; prove engine-pluggable by adding Postgres or MySQL adapter.
5) Harden prompts, add clarifications flow, tune error taxonomy, and expand dialect coverage (SQL Server, Oracle, BigQuery) using capability flags.

## Next Steps
- Start with SQLite datasource profile for local dev/tests; provide sample schema and NL→SQL golden pairs.
- Define schema and plan JSON schemas to lock prompts and tests.
- Wire OpenTelemetry (OTLP collector + chosen APM) and optionally LangSmith export; seed golden NL-SQL pairs for evals.
- Plan the first non-SQLite adapter to prove pluggability (e.g., Postgres) and its driver/config.
