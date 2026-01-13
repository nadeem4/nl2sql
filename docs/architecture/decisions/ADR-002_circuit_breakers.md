# ADR-002: Circuit Breaker Pattern (ENH-002)

## 1. Problem Statement

The NL2SQL Agent relies on multiple unreliable downstream dependencies:

1. **LLM Providers** (OpenAI/Anthropic): Subject to rate limits, latency spikes, and 503 outages.
2. **Vector Stores** (Chroma/Weaviate): Network I/O, potential lock contention.
3. **SQL Databases**: Network partitions, connection pool exhaustion, "Hard Down" instances.

### The Risks

* **Cascading Failure**: If the LLM is down, 100 concurrent users will launch 100 requests. If we just retry, we amplify the load (Retry Storms).
* **Resource Exhaustion**: Threads hanging on `socket.recv` block the entire web server, even for health checks.
* **Poor UX**: Users wait 60s+ for a timeout instead of getting an immediate "Service Unavailable" message.
* **Degraded Functionality**: Without isolation, a DB failure kills the entire agent, even if the user only wanted to chat or check schema.

## 2. Options Analysis

### Option A: Retries Only (Current State)

We currently implement Exponential Backoff.

* **Pros**: Simple. Handles transient blips.
* **Cons**: catastrophic during full outages. If OpenAI is down for 1 hour, every request still tries 3-5 times, wasting CPU and I/O.

### Option B: In-Process Circuit Breaker (`pybreaker`) - **Recommended**

Use a Python library to track failure rates in memory.

* **Mechanism**:
  * **Closed**: Normal operation.
  * **Open**: After `fail_max` errors, fail immediately (raise `CircuitBreakerError`). Avoid downstream calls.
  * **Half-Open**: After `reset_timeout`, let one request through to test the waters.
* **Pros**: lightweight, easy to integrate via decorators, no extra infrastructure.
* **Cons**: State is per-process (not distributed). Each pod must rediscover the outage independently.

### Option C: Service Mesh (Istio / Linkerd)

Offload resilience to the network layer (Envoy sidecars).

* **Pros**: Distributed, language-agnostic, central control.
* **Cons**: Massive operational overkill for the current scale. Depends on k8s deployment.

## 3. Decision

We will implement **Option B** using the `pybreaker` library.

### Rationale

* **Complexity**: Low. It requires only a decorator on service methods.
* **Granularity**: We can define different breakers for different failure domains (e.g., `LLM_BREAKER`, `DB_BREAKER`).
* **Dependencies**: Adds one small pure-Python dependency.

## 4. Implementation Details

### Thresholds & Classification

**LLM Breaker (`LLM_BREAKER`)**:

* `fail_max = 5`: Open after 5 consecutive failures.
* `reset_timeout = 60s`: Wait 1 minute.
* **Failure Classification**:
  * **Failures**: Network Errors, Timed Out, 5xx (Server Error).
  * **Ignored**: 429 (Rate Limit), 400 (Bad Request), 401 (Auth). *Rationale: Rate limits are soft failures and should trigger backoff, not circuit breaking.*

**DB/Vector Breaker (`DB_BREAKER`, `VECTOR_BREAKER`)**:

* `fail_max = 5`
* `reset_timeout = 30s` (Faster recovery for infra blips).
* **Failure Classification**: Any unhandled exception or connection error.

### State Transitions (Critical)

* **Closed -> Open**: Triggered by `fail_max` consecutive errors.
* **Open -> Half-Open**: After `reset_timeout` passes.
* **Half-Open -> Closed**: If the *single probe request* succeeds.
* **Half-Open -> Open**: If the *single probe request* fails.
* **Invariant**: The transition to Half-Open MUST be protected by a lock to prevent a Thundering Herd (only ONE request acts as the probe).

### Tiered Resilience (Degradation Strategy)

We will implement "Graceful Degradation" rather than "Hard Stop" when a breaker opens:

| Breaker Open | Impact | Fallback Behavior |
| :--- | :--- | :--- |
| **DB** | Cannot execute SQL | **Accept Query**: Validate intent via LLM. <br> **Response**: "I understood your query, but the database is currently unreachable. Here is the SQL I would have run: ..." |
| **Vector** | Cannot route/search | **Static Route**: Fallback to direct text search (if available) or warn user. <br> **LLM**: Proceed with "Best Effort" routing without examples. |
| **LLM** | Cannot understand | **Critical Failure**: Return "Service Unavailable. Please try again later." |

### Observability

Circuit breaker state changes must be exported as metrics for alerting (SLO dashboards):

* `breaker_open_total{type="llm|db|vector"}`
* `breaker_half_open_total{type="llm|db|vector"}`
* `breaker_closed_total{type="llm|db|vector"}`
* `breaker_failure_total{type="...", error="timeout|503"}`
* `breaker_ignored_failure_total{type="...", error="429|400"}`

## 5. Integration with Sandbox

The Circuit Breaker sits **upstream** of the Sandbox:
`Agent -> Circuit Breaker -> Sandbox -> SQL Driver`

This ensures that:

1. **Crash Isolation**: Sandbox handles Segfaults.
2. **Failure Isolation**: Breaker handles Timeouts/Network Partitions.

### Scope

We will wrap the following critical paths:

1. `LLMService.invoke()`: Protects against AI provider outages.
2. `VectorStore.search()`: Protects against Retrieval outages.
3. `Adapter.connect()`: Protects against SQL DB outages.

## 5. Consequences

* **Positive**:
  * **Fail Fast**: Usage during outages will return <10ms errors instead of 30s timeouts.
  * **Reduced Load**: Downstream services get breathing room to recover.
  * **Negative**:
  * **Debugging**: Developers must distinguish between "Service Down" and "Circuit Open" errors.
  * **Local State**: Process restarts reset the breaker state.
