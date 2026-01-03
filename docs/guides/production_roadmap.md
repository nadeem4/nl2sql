# Production Roadmap

Steps to transition from MVP to Mission-Critical Service.

## Phase 1: Security & Governance

* [ ] **Secret Management**: Move credentials from `datasources.yaml` to AWS Secrets Manager / Azure Key Vault.
* [ ] **Dependency Locking**: Generate `poetry.lock` or `uv.lock`.
* [ ] **SQL Governance**: Implement a "Policy Agent" to block request types (e.g. `CROSS JOIN` on large tables).

## Phase 2: Reliability

* [ ] **Circuit Breakers**: Enforce strict timeouts (e.g. 5s) at the driver level.
* [ ] **Rate Limiting**: Implement per-tenant token budgeting.

## Phase 3: Observability

* [ ] **Audit Logging**: Write immutable execution logs (User, Query, SQL) to compliance storage.
* [ ] **Async Concurrency**: Refactor Core to `ainvoke` for higher throughput.
