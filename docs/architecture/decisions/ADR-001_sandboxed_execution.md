# ADR-001: Sandboxed Execution & Indexing Plane (ENH-001)

## 1. Problem Statement

### The Core Issue: "Blast Radius" & Reliability

Currently, the NL2SQL Agent executes SQL queries via `ExecutorNode`, performs dry-runs via `PhysicalValidatorNode`, and indexes schemas via `OrchestratorVectorStore` **in-process**.

This couples the stability of the Agent to the stability of the underlying SQL Drivers and the Database itself.

* **Risk 1: Application Crashes (Segfaults)**
  * SQL Drivers (ODBC/C-Ext) running in the main process can trigger Segmentation Faults on malformed data or driver bugs.
  * **Impact**: The entire Agent process dies, terminating all concurrent user sessions.

* **Risk 2: Resource Starvation (The "Noisy Neighbor")**
  * Schema Indexing (`fetch_schema`) is a heavy, long-running I/O and CPU operation.
  * **Impact**: If run in the main event loop, it starves query execution, causing timeouts for other users.

* **Risk 3: Security (Defense in Depth)**
  * **True Risk**: If a driver vulnerability allows RCE, the attacker gains the privileges of the Agent (access to all secrets/envs).
  * **Mitigation Goal**: We want to run execution in a lower-privilege context.

## 2. Non-Goals

* **Prompt Injection**: This architecture does not solve LLM jailbreaks (handled by `IntentValidator`).
* **Semantic Correctness**: We do not guarantee the SQL is "right", only that executing it won't crash the Agent.
* **Query Optimization**: We are not rewriting queries, just running them safely.

## 3. Options Analysis

### Option A: Python Multiprocessing (Process Pool) - **Recommended**

Spawn worker processes on the same machine (Process Boundary Isolation).

* **Mechanism**:
  * **Execution Pool**: A low-latency pool for `ExecutorNode` and `PhysicalValidatorNode` (User-facing queries).
  * **Indexing Pool**: A separate, lower-priority pool for `OrchestratorVectorStore` (Background tasks).
* **Controls**:
  * `max_workers`: Hard cap on concurrency to prevent OOM.
  * `initializer`: Setup persistent global `SQLAlchemy Engine` for connection pooling.
  * `timeouts`: Hard kill signals for stuck processes.
* **Pros**:
  * **Crash Safety**: Parent catches `BrokenProcessPool` and survives.
  * **Performance**: ~1-10ms overhead (warm worker).
  * **Simplicity**: Deployment agnostic (works on Local, Docker, K8s).
* **Cons**:
  * **Weak Security**: Workers share the same FileSystem and Environment (container) as the Agent context unless explicitly scrubbed.
  * **Coupled Scaling**: Validating 1000 queries requires scaling the Agent pods.

### Option B: Dedicated Execution Service (Microservice)

Deploy a separate, independently scalable service (Network Boundary Isolation).

* **Mechanism**: A FastAPI/gRPC service running in a separate deployment (`execution-service`).
* **Pros**:
  * **Strong Security**: Separate Container, separate IAM role, separate filesystem.
  * **Independent Scaling**: Scale Execution (50 replicas) vs Agent (5 replicas).
  * **Connection Pooling**: Excellent, persistent pooling in long-lived pods.
* **Cons**:
  * **Operational Complexity**: Requires Service Discovery, Network Policy, and separate CD pipelines.
  * **Latency**: ~10ms+ Network I/O overhead.

### Option C: Ephemeral Sandbox ("Spawn-on-Demand" Container)

Spin up a fresh Docker container for *every single query*.

* **Verdict**: **Not viable** for high-throughput real-time apps due to "Connection Storms" (no pooling) and 1s+ startup latency.

## 4. Comparison Matrix

| Feature | Option A: Multiprocessing | Option B: Dedicated Service | Option C: Ephemeral Container |
| :--- | :--- | :--- | :--- |
| **Crash Recovery** | ✅ Excellent (Parent survives) | ✅ Excellent (Agent survives) | ✅ Excellent |
| **Implementation Cost** | 🟢 Low (Code only) | 🟡 Medium (New Repo/Service) | 🔴 High (Orchestration) |
| **Ops Complexity** | 🟢 Low (Single Pod) | 🟠 High (Multi-Container) | 🔴 Very High |
| **Per-Query Latency** | 🚀 ~1-10ms (Warm) | ⚡ ~10ms + Network | 🐢 ~1000ms+ |
| **Connection Pooling** | 🟢 Yes (Persistent Workers) | 🟢 Excellent | 🔴 Impossible |

## 5. Interface Contract (Internal Protocol)

Regardless of the implementation (Process or Service), the interface remains constant:

```python
class ExecutionRequest(BaseModel):
    mode: Literal["execute", "dry_run", "fetch_schema"]
    datasource_id: str
    connection_args: Dict[str, Any]
    sql: str
    parameters: Dict[str, Any]
    limits: Dict[str, int]  # e.g. { "max_rows": 1000, "timeout_sec": 30 }

class ExecutionResponse(BaseModel):
    success: bool
    data: Optional[List[Dict[str, Any]]]  # The rows
    error: Optional[ExecutionError]
    metrics: Dict[str, float]  # { "execution_time_ms": 12.5 }
```

## 6. Recommendation

### Phase 1: Option A (Multiprocessing)

* It solves the critical **Blast Radius** immediately.
* It separates **Indexing** from **Execution** via distinct pools.
* It is "Service-Ready": The `ExecutionRequest` protocol makes migrating to Option B trivial later.

### Phase 2: Option B (Dedicated Service)

* Migrate only when we need to scale Execution independently of the Agent or require strict network segmentation.
