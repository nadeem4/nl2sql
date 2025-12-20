# NL2SQL: Features & Production Readiness Guide

---

## 2. Production Readiness Roadmap

To transition this system from a high-quality MVP to a mission-critical Production service, the following gaps must be addressed.

### Phase 1: Security & Governance (Critical)

* [ ] **Secret Management**:
  * **Current**: Secrets in `configs/datasources.yaml` or `.env`.
  * **Target**: Integrate with AWS Secrets Manager / Azure Key Vault. Remove all plaintext credentials from config files.
* [ ] **SQL Governance (The "Semantic Critic")**:
  * **Current**: Validator checks syntax/schema.
  * **Target**: Implement a "Policy Agent" that enforces business rules:
    * Block unbounded queries (Force `LIMIT`).
    * Block `CROSS JOIN` on large tables.
    * Block access to PII columns without specific authorization.
* [ ] **Dependency Locking**:
  * **Current**: `requirements.txt` with loose ranges.
  * **Target**: Generate `poetry.lock` or `uv.lock` to ensure deterministic builds and prevent supply-chain attacks.

### Phase 2: Reliability & Resilience

* [ ] **Trajectory "Memory" (Fixing Lobotomy)**:
  * **Current**: Planner sees only the *last* error.
  * **Target**: Pass the full execution history (Trajectory) to the Planner so it avoids repeating the same failed strategy in a loop.
* [ ] **Circuit Breakers**:
  * **Current**: Unbounded execution times.
  * **Target**: Enforce strict timeouts (e.g., 5s) at the SQL Driver level.
* [ ] **Fan-Out Limits**:
  * **Current**: Theoretically unlimited sub-queries.
  * **Target**: Hard limit on Decomposer fan-out (e.g., Max 5 concurrent queries) to prevent DoS.

### Phase 3: Observability & operations

* [ ] **Audit Logging**:
  * **Target**: Write immutable audit logs (Who, What, When, SQL Executed) to a separate compliance store.
* [ ] **Cost Controls**:
  * **Target**: Implement Token Budgeting per-request and per-tenant to prevent "Infinite Loop" billing spikes.

---

## 3. Evaluation Criteria (Success Metrics)

| Metric | Threshold | logic |
| :--- | :--- | :--- |
| **Execution Accuracy** | **> 90%** | SQL results match Golden Set reference data. |
| **Pass@1 Rate** | **> 95%** | Valid SQL generated on the first attempt (no retry needed). |
| **Fast Lane Latency** | **< 2.0s** | P95 latency for simple lookups. |
| **Security Score** | **100%** | Zero successful SQL injection attempts; Zero PII leaks. |
