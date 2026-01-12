# Security Architecture

Security is a first-class citizen in the NL2SQL Platform. We implement a **Defense-in-Depth** strategy involving Static Analysis, Execution Sandboxing, and Role-Based Access Control.

## 1. Logical Validation (The Firewall)

Before any SQL is generated or executed, the **Abstract Syntax Tree (AST)** must pass the **Logical Validator**.

### Static Analysis

We perform analysis on the `PlanModel` (AST) to enforce:

* **Read-Only**: Only `SELECT` statements are allowed. `DROP`, `ALTER`, `INSERT` are structurally impossible to represent in the Plan Model.
* **Ordinal Integrity**: Ensures plan structure is valid.
* **Safety Violations**: Any violation triggers `ErrorCode.SECURITY_VIOLATION` (Critical Severity).

### Column Scoping (`ValidatorVisitor`)

A recursive walker traverses the AST to verify that:

* Every column reference `t1.col` resolves to a valid Alias `t1` defined in the Plan.
* The column `col` actually exists in the effective schema of `t1`.
* No ambiguous columns (without aliases) are present if multiple tables share the column name.
* *Failures result in `ErrorCode.COLUMN_NOT_FOUND` or `ErrorCode.INVALID_ALIAS_USAGE`.*

## 2. Authorization (RBAC)

We use a strict **Role-Based Access Control** system defined in `configs/policies.json`.

### Policy Enforcement

The `LogicalValidator` checks the `user_context` against the `RolePolicy`.

```python
# Policy Rule
"role_id": {
    "allowed_datasources": ["sales_db"],
    "allowed_tables": ["sales_db.orders", "sales_db.items"]
}
```

* **Strict Namespacing**: Policies MUST use the `datasource.table` format.
* **Fail-Closed**: If the system cannot determine the `selected_datasource_id` (e.g., ambiguous routing), the Validator fails immediately/closed. It never defaults to "Allow All".

## 3. Physical Validation & Sandboxing

Even after safe SQL is generated, we perform **Physical Validation**.

* **Dry Run**: We execute an `EXPLAIN` (or equivalent) on the generated SQL. This catches semantic errors (e.g., type mismatches) safely.
* **Cost Estimation**: We verify the query won't return > `row_limit` (default 1000) rows. Exceeding this triggers `ErrorCode.PERFORMANCE_WARNING` and stops execution.

## 4. Secrets Management

Secrets are never hardcoded. The `SecretManager` uses a **Provider Pattern**.

* **Resolution**: Secrets are resolved at runtime using `${provider:key}` syntax.
* **Two-Phase Loading**:
    1. **Bootstrap**: Loads `env` provider first.
    2. **Resolution**: Resolves config references (e.g. `${env:DB_PASS}`) before registering subsequent providers.
* **Default Provider**: Environment Variables (`${env:MY_SECRET}`).
* **Extensible**: You can register custom providers (e.g., AWS Secrets Manager, Azure KeyVault).

::: nl2sql.pipeline.nodes.validator.node.LogicalValidatorNode
::: nl2sql.security.policies.RolePolicy
::: nl2sql.secrets.manager.SecretManager
