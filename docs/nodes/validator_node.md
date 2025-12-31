# ValidatorNode

## Purpose

The `ValidatorNode` acts as an automated "Quality Assurance" engine. It validates the proposed SQL plan through three progressively deeper layers of checks:

1. **Static Analysis**: Syntax, Schema existence, Type safety.
2. **Semantic Verification**: "Dry Run" against the real database.
3. **Performance Auditing**: Cost estimation and Query Plan analysis.

This ensures that the Executor only receives queries that are Valid, Safe, and Optimized.

## Logic Flow

```mermaid
graph TD
    A[Input State] --> B{1. Static Analysis?}
    B -- Errors --> Z[Return Errors]
    B -- Pass --> C{2. Dry Run?}
    C -- Errors --> Z
    C -- Pass --> D{3. Performance?}
    D -- > 1M Rows --> E[Add Performance Warning]
    D -- Pass --> F[Success]
    E --> F
```

1. **Static Analysis**: Verifies schemas and logical consistency (e.g. aliasing rules).
2. **Semantic Verification**: Sends SQL to the database (transaction rollback) to catch deep errors.
3. **Performance Auditing**: Estimates rows; if high, analyzes the Explain Plan and warns.

## Validation Layers

### 1. Static Analysis & Policy Check

* **Methods**:
  * `_validate_policy(plan)`: Checks `plan.tables` against `user_context.allowed_tables`.
  * `_validate_static_analysis(plan)`: Syntax, Schema existence, Type safety.
* **Checks**:
  * **Access**: Is the user allowed to query 'salary_data'?
  * **Schema**: Do tables/columns exist in `state.schema_info`?
  * **Logic**: Are aliases valid? Aggregations correct?
  * **Types**: Do literals match column types (e.g. Date formats)?
* **Outcome**: `CRITICAL` or `ERROR` (Hard Fail).

### 2. Semantic Verification (Dry Run)

* **Outcome**: `ERROR` (Hard Fail).

### 3. Performance Auditing

* **Method**: `_validate_performance(sql)`
* **Checks**:
  * `adapter.cost_estimate(sql)`: Checks `estimated_rows`.
  * `adapter.explain(sql)`: Analyzes execution plan.
* **Outcome**: `WARNING` (Soft Fail).
  * If `estimated_rows > 1,000,000`, generates a Performance Warning.
  * The Planner receives this feedback and decides whether to Optimize (add filter/limit) or Proceed.

## Components

* **`DatasourceRegistry`**: To fetch the active adapter for Dry Run/Explain.
* **`SchemaInfo`**: Source of truth for static checks.

## Key Errors & Warnings

* **`TABLE_NOT_FOUND`**: Static check failed.
* **`EXECUTION_ERROR`**: Dry Run failed (e.g. invalid object name).
* **`PERFORMANCE_WARNING`**: Query is expensive (`> 1M rows`).
