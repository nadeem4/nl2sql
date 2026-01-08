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

* **Cost Estimation Safeguard**: Before execution, the system requests a cost estimate from the database adapter. If the estimated rows exceed `row_limit` (configured in `datasources.yaml`, default: 1000), execution is aborted with a `SAFEGUARD_VIOLATION`.
* **Timeouts**: Datasource configurations (`datasources.yaml`) support `statement_timeout_ms` to kill long-running queries at the driver level (Native enforcement for Postgres/MySQL).
* **Payload Size Limit**: The system enforces a strict memory limit (`max_bytes`) on the serialized result set. If the data returned by the adapter exceeds this limit (default: 10MB), the execution is halted to prevent OOM (Out Of Memory) crashes.

## 5. Secret Management

The platform employs a **Pluggable Secret Management** system (`nl2sql.secrets`) to handle sensitive credentials securely.

### Mechanism

* **Configuration**: Secrets providers are defined in `secrets.yaml`.
* **Template Hydration**: Secrets are referenced in other config files (like `datasources.yaml`) using the syntax `${provider_id:key}`.
* **Resolution**: The `SecretManager` resolves these references *before* the configuration is parsed, ensuring that sensitive values are never hardcoded in YAML files or committed to version control.

### Providers

The system supports extensible providers via the `SecretProvider` protocol. You configure them in `secrets.yaml` with a unique `id`.

1. **Environment (`env`)**: Standard lookup (e.g., `${env:DB_PASS}`). Always available.
2. **AWS Secrets Manager**: Defined by type `aws`. (e.g., `${aws-prod:db/pass}`).
3. **Azure Key Vault**: Defined by type `azure`. (e.g., `${azure-main:db-secret}`).
4. **HashiCorp Vault**: Defined by type `hashi`. (e.g., `${vault-internal:secret/data/db:pass}`).

**Dependencies**: Cloud providers require optional extras (`nl2sql-core[aws]`, `nl2sql-core[azure]`, etc.) to keep the core lightweight.

## 6. Configuration Security

### Strict Validation (V3)

Datasource configurations are validated strictly at load time. This ensures:

* **Type Safety**: Malformed integers or booleans are rejected.
* **Field Constraints**: Unknown fields are forbidden, preventing "config injection" or typos.
* **Sanitization**: Passwords and sensitive fields are masked in logs.
* **Adapter Specifics**: Each adapter (e.g., `PostgresAdapter`) defines and validates its own configuration schema requirements.

### 6.1 Policy Definition (`configs/policies.json`)

The application uses **Role-Based Access Control (RBAC)**. The `policies.json` file defines policies keyed by **Role ID** (e.g., `admin`, `analyst`).

**Strict Namespacing Rule**: To prevent namespace collisions, `allowed_tables` MUST use the format `datasource_id.table_name`. Simple table names are not supported.

#### Example

```json
{
  "sales_analyst": {
    "description": "Access to Sales DB only",
    "role": "analyst",
    "allowed_datasources": ["sales_db"],
    "allowed_tables": [
      // Exact Match
      "sales_db.orders",
      
      // Datasource Wildcard
      "sales_db.customers_*"
    ]
  },
  "admin": {
    "description": "Super Admin",
    "role": "admin",
    "allowed_tables": ["*"]
  }
}
```

In CLI execution: `nl2sql run ... --role sales_analyst`.
The application assumes the identity provider has already authenticated the user and assigned this role.

### 6.2 Policy Schema & Validation

Policies are treated as **Configuration Code**. To prevent misconfiguration (e.g., typos, invalid types), the system validates `policies.json` against a strict **Pydantic Schema** at startup.

**Schema (`nl2sql.security.policies`)**:

1. **Strict Typing**: Fields like `allowed_datasources` MUST be lists of strings.
2. **Syntax Enforcement**: `allowed_tables` values are validated ensuring they match the `datasource_id.table_name` or wildcard format.
3. **Fail Fast**: If the configuration is invalid, the application refuses to start, printing a clear error message describing the violation.

### 6.3 Policy Management CLI

You can validate your policy file without running a query using the CLI.

```bash
# Validate Syntax & Integrity
nl2sql policy validate
```

This command performs two checks:

1. **Schema Check**: Validates syntax against the Pydantic model.
2. **Integrity Check**: Verifies that referenced `datasources` and `tables` actually exist in `datasources.yaml`. Users often typo table names; this catches those errors before runtime.
