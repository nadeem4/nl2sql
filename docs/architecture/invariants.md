# Architectural Invariants

## Overview
These invariants capture rules that are enforced by the code paths responsible for planning, validation, and execution. They exist to keep the system deterministic, secure, and stable under malformed input or misconfiguration.

---

## Semantic-Only Subqueries and Post-Combine Ops

### Definition
Sub-query intents and post-combine operations must not contain SQL tokens or physical schema keywords.

### Enforcement Points
- `SubQuery.validate_semantic_only()` in `nl2sql.pipeline.nodes.decomposer.schemas`
- `PostCombineOp.validate_semantic_only()` in `nl2sql.pipeline.nodes.decomposer.schemas`

### Failure Behavior
Raises `ValueError` on any forbidden token detection.

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
- `PlanModel.query_type` literal and `LogicalValidatorNode._validate_static()` in `nl2sql.pipeline.nodes.validator.node`

### Failure Behavior
Pydantic validation errors for extra/invalid fields; `PipelineError` with `SECURITY_VIOLATION` for non-READ queries.

### Why It Exists
Prevents mutation or unknown plan constructs from entering execution.

---

## Plan Ordinals Are Contiguous

### Definition
Ordinal fields for tables, joins, select items, group-by, and order-by must be contiguous starting at 0.

### Enforcement Points
- `LogicalValidatorNode._validate_ordinals()` in `nl2sql.pipeline.nodes.validator.node`

### Failure Behavior
Returns `PipelineError` with `INVALID_PLAN_STRUCTURE`.

### Why It Exists
Ensures deterministic ordering and stable SQL generation.

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
- `ValidatorVisitor` in `nl2sql.pipeline.nodes.validator.node`
- `LogicalValidatorNode._validate_static()` converts visitor errors to `PipelineError`

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
- `RolePolicy.validate_namespace()` in `nl2sql.security.policies`
- `LogicalValidatorNode._validate_policy()` in `nl2sql.pipeline.nodes.validator.node`

### Failure Behavior
`ValueError` for invalid policy format; `PipelineError` with `SECURITY_VIOLATION` if datasource ID is missing or a table is not allowed.

### Why It Exists
Ensures access control boundaries are explicit and enforced.

---

## Datasource Access Is RBAC-Gated

### Definition
Only datasources permitted by RBAC may be selected or resolved.

### Enforcement Points
- `DatasourceResolverNode._get_allowed_datasource_ids()` and `__call__()` in `nl2sql.pipeline.nodes.datasource_resolver.node`

### Failure Behavior
Returns `PipelineError` with `SECURITY_VIOLATION` if no allowed datasource is available.

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
- `GlobalPlannerNode.__call__()` in `nl2sql.pipeline.nodes.global_planner.node`
- `ExecutionDAG._layered_toposort()` in `nl2sql.pipeline.nodes.global_planner.schemas`

### Failure Behavior
Raises `ValueError`, leading to `PipelineError` with `PLANNER_FAILED`.

### Why It Exists
Ensures deterministic and well-ordered aggregation execution.

---

## Relation Schemas Have Unique Column Names

### Definition
Each relation schema’s column names must be unique.

### Enforcement Points
- `RelationSchema.validate_unique_columns()` in `nl2sql.pipeline.nodes.global_planner.schemas`

### Failure Behavior
Raises `ValueError` during schema validation.

### Why It Exists
Prevents ambiguous column outputs in execution DAG nodes.

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

## Categories

- **State**: Plan Model Is Strict and Read-Only; Relation Schemas Have Unique Column Names; Context Requires Vector Store and Schema Store Configuration
- **Execution**: SQL Execution Requires SQL, Datasource, and Capability; Execution DAG Must Be Valid and Acyclic; Aggregation Requires Scan Artifacts and Single-Input Post Nodes; Pipeline Execution Is Time-Bounded
- **Security**: Policy Enforcement Is Namespaced and Fail-Closed; Datasource Access Is RBAC-Gated; Plan Model Is Strict and Read-Only
- **Determinism**: Plan Ordinals Are Contiguous; Expected Schema Must Match Select List; Joins Must Be Valid and Schema-Backed
- **Isolation**: SQL Generation Enforces a Row Limit Cap; Context Requires Vector Store and Schema Store Configuration

---

## Gaps

- Column existence enforcement can degrade to warnings when `logical_validator_strict_columns` is disabled, so missing columns do not always block execution.
- Schema version mismatch handling is policy-driven and may only emit warnings (e.g., `schema_version_mismatch_policy=warn`), so mismatch is not always enforced as a hard failure.
- Semantic-only checks are token based; they block a fixed list of SQL tokens rather than parsing for all possible SQL constructs.
- If the vector store is unavailable, datasource resolution can return a response without errors, relying on downstream stages to detect missing candidates.

---

## Related Code

- `packages/core/src/nl2sql/pipeline/nodes/decomposer/schemas.py`
- `packages/core/src/nl2sql/pipeline/nodes/ast_planner/schemas.py`
- `packages/core/src/nl2sql/pipeline/nodes/validator/node.py`
- `packages/core/src/nl2sql/pipeline/nodes/datasource_resolver/node.py`
- `packages/core/src/nl2sql/pipeline/nodes/generator/node.py`
- `packages/core/src/nl2sql/pipeline/nodes/executor/node.py`
- `packages/core/src/nl2sql/execution/executor/sql_executor.py`
- `packages/core/src/nl2sql/pipeline/nodes/global_planner/node.py`
- `packages/core/src/nl2sql/pipeline/nodes/global_planner/schemas.py`
- `packages/core/src/nl2sql/aggregation/aggregator.py`
- `packages/core/src/nl2sql/context.py`
- `packages/core/src/nl2sql/schema/store.py`
- `packages/core/src/nl2sql/pipeline/runtime.py`
- `packages/core/src/nl2sql/security/policies.py`
