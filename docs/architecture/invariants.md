# Architectural Invariants

## Overview
These invariants capture rules that are enforced by the code paths responsible for planning, validation, and execution. They exist to keep the system deterministic, secure, and stable under malformed input or misconfiguration.

---

## Semantic-Only Subqueries and Post-Combine Ops

### Definition
Sub-query intents and post-combine operations must not contain SQL. Each text field (intent, metric names, filter, group-by and order-by attributes, expected-schema names) is checked on its own and rejected if it opens with a `SELECT ... FROM` statement, or contains a statement terminator (`;`) or a comment marker (`--`, `/*`, `*/`). English words that happen to be SQL keywords are allowed: "customers from Brazil" and "tracks where the genre is Rock" are valid intents.

### Enforcement Points
- `SubQuery.validate_semantic_only()` in `nl2sql.pipeline.nodes.decomposer.schemas`
- `PostCombineOp.validate_semantic_only()` in `nl2sql.pipeline.nodes.decomposer.schemas`

### Failure Behavior
Raises `ValueError` ("contains SQL syntax") when a field matches; the decomposer response fails validation.

### Why It Exists
Prevents physical SQL leakage into semantic planning stages and keeps decomposer output safe to interpret downstream.

---

## Combine Groups Require Roles and Join Keys

### Definition
Combine groups with `compare` or `join` operations must include a role for every input and at least one join key pair.

### Enforcement Points
- `CombineGroup.validate_roles()` in `nl2sql.pipeline.nodes.decomposer.schemas`

### Failure Behavior
Raises `ValueError` when roles or join keys are missing.

### Why It Exists
Ensures combine operations are well-defined and deterministic for aggregation.

---

## Decomposer References Must Resolve

### Definition
Combine-group inputs must reference existing subqueries, and post-combine ops must reference existing combine groups.

### Enforcement Points
- `DecomposerResponse.validate_references()` in `nl2sql.pipeline.nodes.decomposer.schemas`

### Failure Behavior
Raises `ValueError` for unknown subquery or group references.

### Why It Exists
Prevents invalid execution graphs and dangling references.

---

## Expression AST Must Match Its Kind

### Definition
Expression nodes must satisfy kind-specific required fields (e.g., binary ops require left/right and an operator).

### Enforcement Points
- `Expr.model_post_init()` in `nl2sql.pipeline.nodes.ast_planner.schemas`

### Failure Behavior
Raises `ValueError` when required fields are missing or operators are invalid.

### Why It Exists
Guarantees the AST is structurally complete before SQL generation.

---

## Plan Model Is Strict and Read-Only

### Definition
Plan fields are schema-validated with no unknown fields, and query type must be `READ`.

### Enforcement Points
- `ConfigDict(extra="forbid")` on plan models in `nl2sql.pipeline.nodes.ast_planner.schemas`
- `PlanModel.query_type: Literal["READ"]` in `nl2sql.pipeline.nodes.ast_planner.schemas`, which pydantic enforces before any node sees the plan (the plan cache re-validates on read, so a cached plan is covered too)

### Failure Behavior
Pydantic validation errors for extra/invalid fields and for any `query_type` other than `READ`; the plan never reaches the validator. `LogicalValidatorNode` no longer re-checks it — the branch was unreachable.

### Why It Exists
Prevents mutation or unknown plan constructs from entering execution.

---

## Table Aliases Are Unique

### Definition
Each table alias in the plan must be unique.

### Enforcement Points
- `LogicalValidatorNode._alias_collision()` in `nl2sql.pipeline.nodes.validator.node`

### Failure Behavior
Returns `PipelineError` with `INVALID_PLAN_STRUCTURE`.

### Why It Exists
Prevents ambiguous column resolution.

---

## Plan Functions Are Known Functions

### Definition
Every `func` expression names a function from `ALLOWED_FUNCTIONS` in `nl2sql.pipeline.nodes.ast_planner.functions`, written as a plain identifier (any case).

### Enforcement Points
- `LogicalValidatorNode._unsupported_functions()` in `nl2sql.pipeline.nodes.validator.node`

### Failure Behavior
Returns `PipelineError` with `UNSUPPORTED_FUNCTION` (severity `ERROR`, so the refiner can retry).

### Why It Exists
`func_name` is model text that becomes the function's name in the SQL. Without the check, any text, including a second statement, reached the query.

---

## Expected Schema Must Match Select List

### Definition
When `expected_schema` is provided, the plan’s `select_items` count and aliases must match it.

### Enforcement Points
- `LogicalValidatorNode._validate_static()` in `nl2sql.pipeline.nodes.validator.node`

### Failure Behavior
Returns `PipelineError` with `INVALID_PLAN_STRUCTURE`.

### Why It Exists
Keeps multi-stage subqueries contractually aligned.

---

## Column References Must Resolve

### Definition
All column references must map to declared table aliases and be unambiguous.

### Enforcement Points
- `LogicalValidatorNode._validate_columns()` in `nl2sql.pipeline.nodes.validator.node`, which resolves the plan with `sqlglot.optimizer.qualify.qualify()` against the schema retrieved for the query
- `LogicalValidatorNode._validate_static()` converts resolution failures to `PipelineError`

### Failure Behavior
Returns `PipelineError` with `COLUMN_NOT_FOUND` (severity depends on `logical_validator_strict_columns`).

### Why It Exists
Prevents queries from referencing non-existent or ambiguous columns.

---

## Joins Must Be Valid and Schema-Backed

### Definition
Join aliases must exist, join conditions must reference both sides, must include an equality pair, and must match allowed schema relationships.

### Enforcement Points
- `LogicalValidatorNode._validate_static()` join checks in `nl2sql.pipeline.nodes.validator.node`

### Failure Behavior
Returns `PipelineError` with `INVALID_PLAN_STRUCTURE` or `JOIN_TABLE_NOT_IN_PLAN`.

### Why It Exists
Prevents invalid joins and enforces schema-authorized relationships.

---

## Policy Enforcement Is Namespaced and Fail-Closed

### Definition
Allowed tables must be namespaced as `datasource.table` or `datasource.*`, and policy checks fail if datasource ID is missing.

### Enforcement Points
- `RolePolicy.validate_namespace()` in `nl2sql.auth.models`
- `LogicalValidatorNode._validate_policy()` in `nl2sql.pipeline.nodes.validator.node`

### Failure Behavior
`ValueError` for invalid policy format; `PipelineError` with `SECURITY_VIOLATION` if datasource ID is missing or a table is not allowed. An unknown role, or no role, allows nothing and is refused the same way, never with `VALIDATOR_CRASH`. The user-facing refusal names no table unless `RBAC_REFUSAL_NAMES_TABLES` is set; the table and roles are in the error's `details`.

### Why It Exists
Ensures access control boundaries are explicit and enforced.

---

## Datasource Access Is RBAC-Gated

### Definition
Only datasources permitted by RBAC may be selected or resolved.

### Enforcement Points
- `DatasourceResolverNode._get_allowed_datasource_ids()` and `__call__()` in `nl2sql.pipeline.nodes.datasource_resolver.node`

### Failure Behavior
Returns `PipelineError` with `SECURITY_VIOLATION` if no allowed datasource is available. The check runs before the resolver's answerability LLM call, so a denied caller triggers no model call and the model only ever sees datasources the role may read. It runs on every path, including the single-datasource shortcut that skips the vector search.

### Why It Exists
Prevents execution against unauthorized datasources.

---

## SQL Execution Requires SQL, Datasource, and Capability

### Definition
Execution only proceeds when SQL text exists, a datasource ID is present, and the datasource supports SQL.

### Enforcement Points
- `ExecutorNode.__call__()` in `nl2sql.pipeline.nodes.executor.node`
- `SqlExecutorService.validate_request()` in `nl2sql.execution.executor.sql_executor`

### Failure Behavior
Returns `PipelineError` with `MISSING_SQL`, `MISSING_DATASOURCE_ID`, or `INVALID_STATE`.

### Why It Exists
Prevents invalid execution requests and ensures capability compatibility.

---

## Execution DAG Must Be Valid and Acyclic

### Definition
Post-combine ops must target known combine groups, all edges must reference existing nodes, and the DAG must be acyclic.

### Enforcement Points
- `build_execution_dag()` in `nl2sql.pipeline.nodes.decomposer.dag`, called by `DecomposerNode.__call__()`
- `ExecutionDAG._layered_toposort()` in `nl2sql.execution.dag`

### Failure Behavior
Raises `ValueError`, leading to `PipelineError` with `PLANNER_FAILED`.

### Why It Exists
Ensures deterministic and well-ordered aggregation execution.

---

## Aggregation Requires Scan Artifacts and Single-Input Post Nodes

### Definition
Scan nodes must have corresponding artifacts, and post-combine nodes must have exactly one input.

### Enforcement Points
- `AggregationService.execute()` in `nl2sql.aggregation.aggregator`

### Failure Behavior
Raises `ValueError` when artifacts are missing or post nodes have invalid inputs.

### Why It Exists
Guarantees aggregation operates on complete, correctly wired inputs.

---

## Context Requires Vector Store and Schema Store Configuration

### Definition
Vector store collection/path and schema store path (for SQLite backend) must be configured.

### Enforcement Points
- `NL2SQLContext.__init__()` in `nl2sql.context`
- `build_schema_store()` in `nl2sql.schema.store`

### Failure Behavior
Raises `ValueError` if required settings are missing.

### Why It Exists
Prevents startup with incomplete indexing and schema storage configuration.

---

## SQL Generation Enforces a Row Limit Cap

### Definition
Generated SQL must not exceed the datasource adapter’s row limit.

### Enforcement Points
- `GeneratorNode.__call__()` in `nl2sql.pipeline.nodes.generator.node`

### Failure Behavior
No error; the limit is clamped to the adapter’s maximum.

### Why It Exists
Protects execution resources and prevents runaway result sizes.

---

## Pipeline Execution Is Time-Bounded

### Definition
Pipeline execution must complete within `settings.global_timeout_sec`.

### Enforcement Points
- `run_with_graph()` in `nl2sql.pipeline.runtime`

### Failure Behavior
Returns `PipelineError` with `PIPELINE_TIMEOUT` and a timeout response message.

### Why It Exists
Ensures latency bounds and prevents hung requests.

---

## Package Boundaries Are One-Way

### Definition
Dependencies point in one direction: `nl2sql-adapter-sdk` → nothing, `nl2sql.adapters.*` → the SDK, `nl2sql` (the engine) → the SDK, and every caller — the REST API, the CLI, the playground — → the top-level `nl2sql` facade.

Concretely:

- The SDK imports only pydantic and the standard library.
- An adapter imports only the SDK, its driver, SQLAlchemy, sqlglot, pydantic and the standard library. SQLAlchemy and sqlglot are adapter-layer libraries: the shared connection layer, and the expression vocabulary the engine hands over to be rendered.
- Nothing outside `nl2sql/adapters/` imports an adapter, SQLAlchemy or a database driver. The engine reaches an adapter through the `nl2sql.adapters` entry points and `DatasourceAdapterProtocol`.
- Nothing outside `nl2sql/cli/` imports the CLI, and `nl2sql.cli.demo` never imports `nl2sql.cli.commands`.
- `nl2sql_api` imports only the top-level `nl2sql` namespace, and the playground reaches the engine only through `NL2SQL`'s public methods, never `engine.context`.
- The runtime (`pipeline`, `execution`, `indexing`, `datasources`, `llm`, `auth`, `aggregation`, `schema`, `services`, `context`, `public_api`) never imports `nl2sql.evaluation` or `nl2sql.feedback` at module scope. A deferred import inside a function is fine.
- A provider's API-key environment variable is read in `nl2sql/llm/` only. `PROVIDER_PRESETS` (`llm/registry.py`) is the one table; everything the CLI, the playground, evaluation and the REST API know about providers derives from it through `llm/providers.py`.

### Enforcement Points
- `packages/nl2sql/tests/architecture/test_boundaries.py` — an AST import scan of `packages/*/src`, one test per rule
- `packages/api/tests/test_architecture.py` — the REST API's own imports, and that its response model *is* `nl2sql.QueryResult`

### Failure Behavior
A failing test naming the rule and the file, line and import that broke it. Each documented exception is a named constant in the test module with the reason beside it.

### Why It Exists
Each rule keeps one thing replaceable. The SDK is what a third-party adapter compiles against, so a dependency there is a dependency every adapter author inherits; an adapter that imported the engine would close the circle and make the split meaningless. Engine code that imports an adapter, or SQLAlchemy, is a second way to reach a database that no new adapter can plug into. And `import nl2sql` loading `nl2sql.evaluation` cost every SDK user the YAML loaders, the gold dataset and the fake LLM before anyone asked a question.

### Example
The playground once read `engine.context.ds_registry`, `engine.context.schema_store` and `engine.context.vector_store` through `getattr`, so the schema view, the retrieval inspector and index health existed only in the playground; `nl2sql-api` had a placeholder where index status should be. The fix was four facade methods that both callers use. The rule stops the shortcut being taken again, which is what keeps a second client cheap.

---

## Dialect Knowledge Lives in the Adapter

### Definition
No engine module names a database dialect or writes one database's SQL. The plan says *what*; the adapter says *how*. An adapter declares its dialect with `get_dialect()`, which must return a name `sqlglot.Dialect.get_or_raise` accepts, and may override `render_sql()` for what sqlglot cannot express.

### Enforcement Points
- `test_no_dialect_name_outside_the_adapters` and `test_no_dialect_specific_sql_outside_the_adapters` in `packages/nl2sql/tests/architecture/test_boundaries.py` — a scan of every non-docstring string literal, f-string chunks included
- `test_get_dialect_is_a_sqlglot_dialect`, over every `nl2sql.adapters` entry point
- The plan's one function vocabulary: `ALLOWED_FUNCTIONS` in `nl2sql.pipeline.nodes.ast_planner.functions` (see *Plan Functions Are Known Functions*)

### Failure Behavior
A failing test naming the literal and where it was written. The documented exceptions are the engine's own SQLite metadata store (`schema/store.py`, `schema/sqlite_store.py`, `common/settings.py`), the in-process polars/DuckDB compute engine (`aggregation/engines/polars_duckdb.py`), and the plan's function allow-list.

### Why It Exists
SQL that only one database understands, written in the engine, is wrong for every other database — and it is wrong silently, because it parses. A dialect name in engine code is worse: it is a switch statement that every future adapter has to be added to, in a repo whose point is that adapters are pluggable.

### Example
The planner prompt asked for `STRFTIME('%Y', o.order_date)` to get a year. On SQLite that works; on Postgres and SQL Server it is a call to a function that does not exist, and on MySQL it means something else. The fix was not to teach the prompt about dialects but to move the decision: the plan asks for `DATE_PART('year', x)`, the generator builds a typed sqlglot node, and `SQLiteAdapter.render_sql()` rewrites it to `STRFTIME` for SQLite only. Related: `get_dialect()` returned SQLAlchemy's `postgresql` and `mssql`, which sqlglot rejects, so *no* SQL could be generated for either datasource — a one-word bug that the entry-point contract test now catches.

---

## The Validator Rejects Everything the Generator Can

### Definition
Anything the generator can refuse, the logical validator must refuse first. The validator is the last node with a retry edge back to the planner; the generator runs after it.

### Enforcement Points
- `packages/nl2sql/tests/unit/test_logical_validator_generator_parity.py` — each case asserts both halves: the generator rejects the plan, and so does the validator, with a retryable code
- `LogicalValidatorNode._validate_static()` in `nl2sql.pipeline.nodes.validator.node`

### Failure Behavior
A structural mistake is returned as a retryable `PipelineError` from the validator, so `check_logical_validation` sends the plan back to the refiner and the planner sees the message. The same mistake reaching the generator is a terminal `SQL_GEN_FAILED`.

### Why It Exists
A check that lives only in the generator is a dead end rather than a retry. The model produced a fixable plan and got a hard failure, one node past the only gate that could have asked it to try again.

### Example
The validator resolves columns against a throw-away query in which every table is `CROSS JOIN`ed — correct for column resolution, and deliberately blind to join topology. So a plan declaring three tables and joining only two validated CLEAN, then failed in the generator with *"Table alias(es) t3 are declared in the plan but never joined to the FROM table."* Three more join-shape mistakes behaved the same way. Every one is a pure function of `plan.tables` and `plan.joins` — no schema, no dialect — so each belongs in the validator, where the message reaches the planner.

---

## Categories

- **State**: Plan Model Is Strict and Read-Only; Context Requires Vector Store and Schema Store Configuration
- **Execution**: SQL Execution Requires SQL, Datasource, and Capability; Execution DAG Must Be Valid and Acyclic; Aggregation Requires Scan Artifacts and Single-Input Post Nodes; Pipeline Execution Is Time-Bounded
- **Security**: Policy Enforcement Is Namespaced and Fail-Closed; Datasource Access Is RBAC-Gated; Plan Model Is Strict and Read-Only
- **Determinism**: Expected Schema Must Match Select List; Joins Must Be Valid and Schema-Backed
- **Resource bounds**: SQL Generation Enforces a Row Limit Cap; Context Requires Vector Store and Schema Store Configuration
- **Boundaries**: Package Boundaries Are One-Way; Dialect Knowledge Lives in the Adapter
- **Retryability**: The Validator Rejects Everything the Generator Can; Plan Functions Are Known Functions

---

## Gaps

- Column existence enforcement can degrade to warnings when `logical_validator_strict_columns` is disabled, so missing columns do not always block execution.
- Schema version mismatch handling is policy-driven and may only emit warnings (e.g., `schema_version_mismatch_policy=warn`), so mismatch is not always enforced as a hard failure.
- Semantic-only checks are pattern based: they block a field that opens with `SELECT ... FROM`, a statement terminator and comment markers, rather than parsing for every possible SQL construct. An intent that literally reads "select ... from ..." is rejected as SQL.
- If the vector store is unavailable, datasource resolution can return a response without errors, relying on downstream stages to detect missing candidates.

---

## Related Code

- `packages/nl2sql/src/nl2sql/pipeline/nodes/decomposer/schemas.py`
- `packages/nl2sql/src/nl2sql/pipeline/nodes/ast_planner/schemas.py`
- `packages/nl2sql/src/nl2sql/pipeline/nodes/validator/node.py`
- `packages/nl2sql/src/nl2sql/pipeline/nodes/datasource_resolver/node.py`
- `packages/nl2sql/src/nl2sql/pipeline/nodes/generator/node.py`
- `packages/nl2sql/src/nl2sql/pipeline/nodes/executor/node.py`
- `packages/nl2sql/src/nl2sql/execution/executor/sql_executor.py`
- `packages/nl2sql/src/nl2sql/pipeline/nodes/decomposer/dag.py`
- `packages/nl2sql/src/nl2sql/execution/dag.py`
- `packages/nl2sql/src/nl2sql/aggregation/aggregator.py`
- `packages/nl2sql/src/nl2sql/context.py`
- `packages/nl2sql/src/nl2sql/schema/store.py`
- `packages/nl2sql/src/nl2sql/pipeline/runtime.py`
- `packages/nl2sql/src/nl2sql/auth/models.py`
- `packages/nl2sql/src/nl2sql/pipeline/nodes/ast_planner/functions.py`
- `packages/nl2sql/src/nl2sql/llm/registry.py`
- `packages/adapter-sdk/src/nl2sql_adapter_sdk/protocols.py`
- `packages/nl2sql/tests/architecture/test_boundaries.py`
