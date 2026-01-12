# Nodes & Pipeline

The pipeline is composed of specialized **Nodes**. Each node performs a single responsibility and is designed to validatable independently.

## 1. Semantic Analysis Node

**Responsibility**: Pre-processing and Intent Classification.

* **Canonicalization**: Corrects spelling and formats entities.
* **Enrichment**: Adds synonyms and keywords to aid retrieval.

## 2. Decomposer Node (The Router)

**Responsibility**: Complexity analysis and Datasource Routing.

* **Retrieval**: Fetches relevant schemas and few-shot examples using **Partitioned MMR**.
* **Splitting**: Breaks down complex/ambiguous questions into sub-queries targeted at specific datasources.

## 3. Planner Node

**Responsibility**: Logical Planning (Schema Hydration).

* **Input**: User Query + Schema Context.
* **Output**: `PlanModel` (AST).
* **Logic**: Identifies tables, resolve joins using Foreign Keys, and maps natural language filters to columns.

## 4. Logical Validator

**Responsibility**: Static Analysis & Security.

* **Checks**:
  * Column Existence (scoping aliases).
  * Join Validity (keys exist).
  * Policy Compliance (Reference `safety/security.md`).

## 5. Generator Node

**Responsibility**: Code Generation.

* **Input**: Validated `PlanModel`.
* **Output**: Dialect-specific SQL string.
* **Logic**: Uses Jinja2 templates and strict typing to convert the AST into executable SQL.

## 6. Physical Validator

**Responsibility**: Execution Readiness.

* **Dry Run**: Executes `EXPLAIN` or equivalent to verify syntax validity without running the query.
* **Performance**: Checks row cost estimates against `row_limit`.

## 7. Executor Node

**Responsibility**: Sandboxed Execution.

* **Logic**: Connects to the database using the specific Adapter and executes the query.
* **Safety**: Read-only connection enforcement.

## 8. Refiner Node

**Responsibility**: Self-Correction.

* **Trigger**: Activation on Validator or Executor errors.
* **Logic**: Analyzes the error stack trace + previous plan, and generates feedback for the Planner to retry.

## 9. Aggregator Node

**Responsibility**: Result Synthesis.

* **Fast Path**: If single result set & no errors -> Return Data.
* **Slow Path**: If multiple results or errors -> Use LLM to summarize and explain.
