# Enterprise NL2SQL Engine

> **A Production-Grade Natural Language to SQL Engine built on the principles of Zero Trust and Deterministic Execution.**

This platform treats "Text-to-SQL" not as a prompt engineering problem, but as a **Distributed Systems** problem. It replaces fragile one-shot generation with a robust, compiled pipeline that bridges the gap between Unstructured Intention (User Language) and Structured Execution (SQL Databases).

---

## 🏗️ System Topology

The architecture is composed of three distinct planes, ensuring separation of concerns and failure isolation.

### 1. The Control Plane (The Graph)

**Responsibility**: Reasoning, Planning, and Orchestration.

* **Agentic Graph**: Implemented as a Directed Cyclic Graph (LangGraph) to enable "Refinement Loops". If a plan fails validation, the system self-corrects.
* **State Management**: Deterministic state transitions ensure auditability and reproducibility of every decision.

### 2. The Security Plane (The Firewall)

**Responsibility**: Invariants Enforcement.

* **Valid-by-Construction**: The LLM *never* executes SQL directly. It generates an **Abstract Syntax Tree (AST)**.
* **Static Analysis**: The [Validator Node](docs/core/nodes.md#4-logical-validator) enforces **Row-Level Security (RLS)** and type safety on the AST *before* compilation.
* **Intent Classification**: Upstream detection of adversarial prompts (Jailbreaks/Injections).

### 3. The Data Plane (The Sandbox)

**Responsibility**: Semantic Search and Execution.

* **Blast Radius Isolation**: SQL Drivers (ODBC/C-Ext) run in a dedicated **[Sandboxed Process Pool](docs/architecture/decisions/ADR-001_sandboxed_execution.md)**. A segfault in a driver kills a disposable worker, not the Agent.
* **Partitioned Retrieval**: The [Orchestrator](docs/core/indexing.md) uses Partitioned MMR to inject only relevant schema context, preventing context window overflow.

---

## 📐 Architectural Invariants

| Invariant | Rationale | Mechanism |
| :--- | :--- | :--- |
| **No Unvalidated SQL** | Prevent Hullucinations & Data Leaks | All plans pass through `LogicalValidator` (AST) + `PhysicalValidator` (Dry Run) before execution. |
| **Zero Shared State** | Crash Safety | Execution happens in isolated processes; no shared memory with the Control Plane. |
| **Fail-Fast** | Reliability | Circuit Breakers and Strict Timeouts prevent cascading failures (Retry Storms). |
| **Determinism** | Debuggability | Temperature-0 generation + Strict Typing (Pydantic) for all LLM outputs. |

---

## 🚀 Quick Start

### Prerequisites

* Python 3.10+
* Docker (Optional, for full integration environment)

### 1. Installation

```bash
git clone https://github.com/nadeem4/nl2sql.git
cd nl2sql

# Set up environment
python -m venv venv
source venv/bin/activate

# Install Core Engine & CLI
pip install -e packages/core
pip install -e packages/cli
pip install -e packages/adapter-sdk
```

### 2. Run Demo (Lite Mode)

Boot the engine with an in-memory SQLite database (No Docker required).

```bash
nl2sql setup --demo
```

---

## 📚 Technical Documentation

* **[System Architecture](docs/core/architecture.md)**: Deep dive into the Control, Security, and Data planes.
* **[Component Reference](docs/core/nodes.md)**: Detailed specs for Planner, Validator, Executor, etc.
* **[Security Model](docs/safety/security.md)**: Defense-in-depth strategy against prompt injection and unauthorized access.
* **[ADR-001: Sandboxed Execution](docs/architecture/decisions/ADR-001_sandboxed_execution.md)**: Decision record for the Process Pool architecture.

---

## 📦 Repository Structure

```text
packages/
├── core/               # The Engine (Graph, State, Logic)
├── cli/                # Terminal Interface & Ops Tools
├── adapter-sdk/        # Interface Contract for new Databases
└── adapters/           # Official Dialects (Postgres, MSSQL, MySQL)
configs/                # Runtime Configuration (Policies, Prompts)
docs/                   # Architecture & Operations Manual
```
