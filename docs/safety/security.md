# Security Architecture

Security is a first-class citizen in the NL2SQL Platform. We implement a **Defense-in-Depth** strategy involving Static Analysis, Execution Sandboxing, and Role-Based Access Control.

## 0. Intent Validation (Layer 0)

Before the pipeline even begins semantic analysis, the **Intent Validator** acts as the primary gatekeeper.

### Purpose

To mitigate **Logic Injection** and **Jailbreak** attacks where users trick the LLM into ignoring instructions or revealing sensitive data.

### Mechanism

The intent validator uses a low-temperature LLM call to classify the user query into one of several categories:

1. **SAFE**: Benign business queries (e.g., "Show me sales").
2. **JAILBREAK**: Attempts to bypass rules (e.g., "Ignore previous instructions").
3. **PII_EXFILTRATION**: Requests for mass dumps of sensitive data without filters.
4. **DESTRUCTIVE**: Attempts to modify data (DROP, DELETE).
5. **SYSTEM_PROBING**: Queries about the system's own prompts or architecture.

**Any result other than SAFE fails immediately** with `ErrorCode.INTENT_VIOLATION`. This node runs *before* the Planner has a chance to see the prompt, providing strong isolation.

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

## 2. Retrieval Security (Scope)

Before the system even attempts to plan, we limit the **Knowledge Scope** available to the LLM. This prevents the "Decomposer" from hallucinating or planning against tables the user cannot see.

### Vector Store Filtering

The `VectorStore` enforces a strict **Metadata Filter** on every retrieval call.

* **Mechanism**: `query({filter: {'datasource_id': {'$in': allowed_ds_ids}}})`
* **Guarantee**: If a user only has access to `sales_db`, vectors from `hr_db` are physically excluded from the search space. The LLM never sees them.

### Decomposer Fail-Safe

The `DecomposerNode` performs an explicit pre-check:

```python
def _check_user_access(state):
    allowed = state.user_context.get("allowed_datasources")
    if not allowed:
        raise SecurityViolation("Access Denied")
```

If the user context has no allowed datasources, the request is rejected immediately with `ErrorCode.SECURITY_VIOLATION`.

## 3. Internal Error Sanitization (Data Leakage)

To prevent leaking schema details, SQL fragments, or connection secrets to the LLM (and potentially the user), the **Aggregator Node** implements an internal firewall for error messages.

### Sanitization Mechanism

Before injecting execution errors into the LLM context for summarization:

1. **Check Error Code**: Identify the type of error (e.g., `DB_EXECUTION_ERROR`, `SAFEGUARD_VIOLATION`).
2. **Sanitize**: If the error type is sensitive, replace the raw message (e.g., `Syntax error at column "password"`) with a safe, generic message (`An internal database error occurred`).
3. **Result**: The LLM works with safe abstractions, while raw errors are preserved in the internal Audit Log for admins.

## 4. Authorization (RBAC)

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

## 5. Physical Validation & Sandboxing

Even after safe SQL is generated, we perform **Physical Validation**.

* **Dry Run**: We execute an `EXPLAIN` (or equivalent) on the generated SQL. This catches semantic errors (e.g., type mismatches) safely.
* **Cost Estimation**: We verify the query won't return > `row_limit` (default 1000) rows. Exceeding this triggers `ErrorCode.PERFORMANCE_WARNING` and stops execution.

## 6. Secrets Management

Secrets are never hardcoded. The `SecretManager` uses a **Provider Pattern**.

* **Runtime Resolution**: Secrets are resolved using `${provider:key}` syntax.
* **Two-Phase Startup**:
    1. **Bootstrap**: The `env` provider is loaded immediately.
    2. **Configuration**: Other providers (AWS, Azure) are configured. These configurations can themselves reference `env` secrets (e.g., `client_secret: "${env:AZURE_SECRET}"`).
* **Extensible**: Supports built-in providers (AWS, Azure, HashiCorp) and custom implementations.

### Configuration (`configs/secrets.yaml`)

You configure providers in the `secrets.yaml` file.

```yaml
version: 1
providers:
  - id: "aws-prod"
    type: "aws"
    region_name: "us-east-1"
  - id: "azure-main"
    type: "azure"
    vault_url: "https://my-vault.vault.azure.net/"
    # You can reference other secrets (Two-Phase Loading)
    client_secret: "${env:AZURE_CLIENT_SECRET}"
```

### Resolution Syntax

Resolves at runtime using `${provider:key}` syntax.

* `${env:DB_PASS}`: Environment variable `DB_PASS`.
* `${aws-prod:rds_password}`: Secret `rds_password` from the `aws-prod` provider.

### Supported Providers

We support the following backend providers out of the box.

#### 1. Environment Variables (`type: env`)

* **Default**: Always available as the `env` provider.
* **Usage**: `${env:VAR_NAME}`.

#### 2. AWS Secrets Manager (`type: aws`)

Fetches secrets from AWS Secrets Manager.

| Config | Description | Default |
| :--- | :--- | :--- |
| `region_name` | AWS Region (e.g. `us-east-1`) | `AWS_DEFAULT_REGION` env var |
| `profile_name` | AWS CLI Profile to use | Default boto3 profile |

#### 3. Azure Key Vault (`type: azure`)

Fetches secrets from Azure Key Vault.

| Config | Description | Required? |
| :--- | :--- | :--- |
| `vault_url` | Full URL to the vault | Yes |
| `client_id` | Service Principal Client ID | Yes |
| `client_secret` | Service Principal Secret | Yes |
| `tenant_id` | Azure Tenant ID | Yes |

#### 4. HashiCorp Vault (`type: hashi`)

Fetches secrets from a HashiCorp Vault KV engine.

| Config | Description | Default |
| :--- | :--- | :--- |
| `url` | Vault Server URL | **Required** |
| `token` | Auth Token | None (Must be provided) |
| `mount_point` | KV engine mount path | `secret` |
