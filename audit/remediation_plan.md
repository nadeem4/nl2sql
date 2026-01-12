# Audit Remediation Plan & Backlog

This document serves as the master backlog for addressing findings from the Architectural Audit.

## 🔴 Critical & High Priority Bugs

- [x] **BUG-001: Logic Injection Vulnerability** (Critical)
  - **Component**: Security / Planner
  - **Issue**: The `Planner` can be tricked via prompt injection to ignoring "Read Only" intent or accessing allowed but sensitive data in unintended ways.
  - **Fix**: Implement a dedicated **Intent Validator** layer separate from the Planner and `LogicalValidator` that specifically checks for adversarial patterns before planning.
  - **Status**: Fixed. Unit tests added in `tests/unit/test_node_intent_validator.py`.

- [x] **BUG-002: Unbounded Retry Storms** (Critical)
  - **Component**: Reliability / Graph
  - **Issue**: `check_planner` logic retries 3 times immediately without backoff. In a high-load or outage scenario, this triples the load on downstream services, causing cascading failure.
  - **Fix**: Implement **Exponential Backoff** and **Jitter** in the `retry_handler` logic within `sql_agent.py`. Added selective retry logic to fail fast on fatal errors.
  - **Status**: Fixed. Unit tests added in `tests/unit/test_sql_agent_retry.py`.

- [x] **BUG-003: Internal Error Leakage** (High)
  - **Component**: Security / Aggregator
  - **Issue**: `AggregatorNode` feeds raw database error strings (which may contain schema details or secrets) into the LLM context.
  - **Fix**: Sanitize or hash non-user-facing errors in `AggregatorNode` before prompt construction. Only show generic error codes to the LLM.
  - **Status**: Fixed. Unit tests added in `tests/unit/test_node_aggregator.py`.

- [x] **BUG-004: Schema Drift (Stale Cache)** (High)
  - **Component**: Governance / Registry
  - **Issue**: `DatasourceRegistry` caches adapters indefinitely at startup. If the DB schema changes, the Planner hallucinates invalid columns.
  - **Fix**: Implemented idempotent `refresh_schema` and `refresh_examples` in `OrchestratorVectorStore`, along with dynamic `register_datasource` in `DatasourceRegistry`.
  - **Status**: Fixed. Unit tests added in `tests/unit/test_schema_lifecycle.py`.

- [x] **BUG-005: Missing Distributed Tracing** (High)
  - **Component**: Observability / Logging
  - **Issue**: Logs lack a unique `trace_id` per request, making concurrent request debugging impossible in multi-threaded environments.
  - **Fix**: Implemented `trace_id` in `GraphState`, `TraceContextFilter` in logger, and `traced_node` wrapper for context propagation.
  - **Status**: Fixed. Verified in `tests/unit/test_tracing.py`.

## 🟡 Medium & Low Priority Bugs

- [ ] **BUG-006: Missing Global Timeout** (Medium)
  - **Component**: Reliability
  - **Issue**: Sub-query branches or long-running chains have no global deadline, risking zombie threads.
  - **Fix**: Add `execution_timeout` to the `Graph.invoke` config and handle `TimeoutError` gracefully.

- [ ] **BUG-007: Ephemeral Cost Tracking** (Medium)
  - **Component**: Operations
  - **Issue**: Token usage logs are stored in a global in-memory variable `token_log`. Data is lost on pod restart.
  - **Fix**: Flush token usage logs to a persistent store (Audit DB or structured log stream).

- [ ] **BUG-008: Non-Deterministic Planning** (Medium)
  - **Component**: Safety
  - **Issue**: `configs/llm.yaml` does not enforce `temperature=0`. Users get different execution plans for the same query.
  - **Fix**: Hardcode `temperature: 0.0` for all Planner/Validator agents in `LLMRegistry`.

- [ ] **BUG-009: Plaintext Secrets in Memory** (Low)
  - **Component**: Security
  - **Issue**: Connection args (including passwords) are passed around in standard unique dictionaries.
  - **Fix**: Wrap sensitive connection args in `SecretStr` (Pydantic) to prevent accidental logging/printing.

## 🚀 Enhancements (Architecture Upgrade)

- [ ] **ENH-001: Sandboxed Execution Service** (P0 - Critical)
  - **Value**: Prevents driver crashes from taking down the Agent and mitigates RCE risks.
  - **Action**: Move `ExecutorNode` logic to a separate gRPC service/sidecar with limited privileges.

- [ ] **ENH-002: Circuit Breaker Pattern** (P0 - Critical)
  - **Value**: Fails fast during outages instead of hanging threads.
  - **Action**: Wrap OpenAI and Database calls with Circuit Breakers (e.g., `pybreaker`).

- [ ] **ENH-003: OpenTelemetry Integration** (P1 - High)
  - **Value**: Enable standard APM features (Datadog/Jaeger) for trace visualization.
  - **Action**: Replace custom `json` logging with OTelSDK.

- [ ] **ENH-004: Persistent Audit Log** (P1 - High)
  - **Value**: Required for Compliance and Regression Testing.
  - **Action**: Create a `request_audit` database table to store Query/Plan/SQL tuples.

- [ ] **ENH-005: Tenant-Aware RLS Middleware** (P2 - Medium)
  - **Value**: Defense-in-depth enforcement of multi-tenancy.
  - **Action**: Implement a SQL transformation layer in `Generator` that automatically injects `WHERE tenant_id = ?` clauses into every generated AST.

- [ ] **ENH-006: Streaming Response Support** (P2 - Medium)
  - **Value**: Improves perceived latency.
  - **Action**: Update `AggregatorNode` to stream tokens to the frontend instead of waiting for full generation.
