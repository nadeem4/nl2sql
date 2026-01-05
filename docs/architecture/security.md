# Security Architecture

The NL2SQL Platform implements a multi-layered security approach designed to ensure that users can only access data they are explicitly authorized to see. This document outlines the security mechanisms for authentication, authorization, and query validation.

## 1. Authentication & Context

The platform assumes that the caller (API or CLI) has already authenticated the user. The user's identity and roles are passed into the execution pipeline via the `user_context` dictionary in the `GraphState`.

### User Context Structure

The `user_context` must contain the following keys to enable authorization checks:

```json
{
  "role": "sales_analyst",
  "allowed_datasources": ["manufacturing_history", "manufacturing_supply"],
  "allowed_tables": [
      "customers", "sales_orders", "products", "inventory"
  ]
}
```

* **role**: A label for logging and auditing purposes.
* **allowed_datasources**: A list of `datasource_id` strings that the user can query. Use `["*"]` for full access (e.g., Admin).
* **allowed_tables**: A list of table names the user can access. This enables fine-grained Row/Table-Level Security (RLS/TLS).

## 2. Authorization Layers

Security checks are performed at two distinct stages of the pipeline to fail fast and prevent unauthorized data access.

### Layer 1: Datasource Access (Routing)

**Component**: `DecomposerNode` & `OrchestratorVectorStore`

Before any query planning begins, the system enforces **Knowledge Isolation**.

* **Logic**:
    1. Extracts `allowed_datasources` from `state.user_context`.
    2. If the list is empty, the request is immediately rejected.
    3. **Vector Store Partitioning**: During context retrieval (RAG), the search is strictly filtered using `filter={"datasource_id": {"$in": allowed_ids}}`.
    4. This ensures that the LLM is **never** exposed to schema definitions or example questions from unauthorized datasources, effectively partitioning the "knowledge base" per user.

### Layer 2: Table Access (Logical Validation)

**Component**: `LogicalValidatorNode`

After the `PlannerNode` generates an abstract syntax tree (AST) for the query, the `LogicalValidatorNode` performs a strict policy check.

* **Logic**:
    1. Extracts distinct table names from the `PlanModel` (AST).
    2. Compares them against `state.user_context["allowed_tables"]`.
    3. If any table in the plan is not in the allowed list, a critical `SECURITY_VIOLATION` error is raised.
    4. The pipeline terminates immediately; the query is never generated or executed.

## 3. Query Safety & Validation

Beyond RBAC, the system enforces strict structural constraints to prevent SQL injection and accidental mutation.

### Read-Only Enforcement

The `LogicalValidatorNode` enforces that all generated plans are strictly `READ` operations (SELECT).

* Mutation statements (INSERT, UPDATE, DELETE, DROP) are structurally impossible to represent in the `PlanModel` AST.
* Even if the planner were to hallucinate a non-read type, the validator acts as a firewall and rejects it.

### Validated AST vs. Raw SQL

The system does **not** rely on the LLM to generate raw SQL strings directly.

1. **Planner**: Generates a typed JSON AST (`PlanModel`).
2. **Validator**: Validates the AST logic and security.
3. **Generator**: Compiles the validated AST into SQL using a deterministic compiler (`Visitor` pattern).

This separation ensures that "Prompt Injection" attacks cannot easily force the model to output malicious SQL syntax, as the Generator controls the final output syntax.

## 4. Resource Protection (DoS)

To prevent Denial of Service (DoS) attacks or run-away queries, the system implements resource safeguards in the `ExecutorNode`.

* **Cost Estimation Safeguard**: Before execution, the system requests a cost estimate from the database adapter. If the estimated rows exceed `SAFEGUARD_ROW_LIMIT` (default: 10,000), execution is aborted with a `SAFEGUARD_VIOLATION`.
* **Timeouts**: Datasource configurations (`datasources.yaml`) support `statement_timeout_ms` to kill long-running queries at the driver level.

## 5. Deployment Security

* **Environment Variables**: Sensitive keys (like `OPENAI_API_KEY`) are loaded strictly from environment variables via `settings.py`, following 12-Factor App best practices. They are never hardcoded in the source.
* **Docker Container**: The application runs in a containerized environment, isolating the runtime from the host system.

## 6. Configuration

User roles and permissions are defined in `users.json` (pointed to by `USERS_CONFIG` setting).

**Example Configuration**:

```json
{
  "guest": {
    "role": "guest",
    "allowed_datasources": ["public_data"],
    "allowed_tables": ["products", "store_locations"]
  },
  "admin": {
    "role": "admin",
    "allowed_datasources": ["*"],
    "allowed_tables": ["*"]
  }
}
```
