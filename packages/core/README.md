# NL2SQL Core

The **NL2SQL Core** (`nl2sql-core`) is the brain of the natural language to SQL engine. It orchestrates the entire query lifecycle using a graph-based agent architecture.

## 🏗️ Architecture Overview

The NL2SQL Core is built around a **graph-based orchestration system** using LangGraph that treats text-to-SQL as a distributed systems problem. The architecture is organized around several key planes:

### 1. **The Control Plane (The Graph)**
- **Responsibility**: Reasoning, Planning, and Orchestration
- **Implementation**: Directed Cyclic Graph (LangGraph) with explicit state (`GraphState`)
- **Features**: Agentic graph with refinement loops for self-correction when plans fail validation

### 2. **The Security Plane (The Firewall)**
- **Responsibility**: Invariants Enforcement
- **Implementation**: Valid-by-Construction approach where LLM generates Abstract Syntax Tree (AST) rather than executing SQL
- **Features**: Static analysis through logical validators enforcing RBAC and schema constraints

### 3. **The Data Plane (The Sandbox)**
- **Responsibility**: Semantic Search and Execution
- **Implementation**: Sandboxed Process Pool for SQL driver isolation
- **Features**: Partitioned retrieval with schema store and vector-based context injection

### 4. **The Reliability Plane (The Guard)**
- **Responsibility**: Fault Tolerance and Stability
- **Implementation**: Layered defense with Circuit Breakers and Sandboxing
- **Features**: Fail-fast approach with strict timeouts preventing cascading failures

### 5. **The Observability Plane (The Watchtower)**
- **Responsibility**: Visibility, Forensics, and Compliance
- **Implementation**: Native OpenTelemetry integration
- **Features**: Distributed tracing (Jaeger), metrics (Prometheus), and forensic audit logs

## 🧠 Key Components

### **Context Management (`context.py`)**
- `NL2SQLContext`: Centralized application context managing initialization lifecycle
- Ensures proper ordering: secrets → datasources → LLMs → policies
- Coordinates all registries and stores

### **Graph Pipeline (`pipeline/`)**
- **Graph Orchestration**: LangGraph-based state machine managing query flow
- **Nodes**: DatasourceResolver, Decomposer, GlobalPlanner, Aggregator, AnswerSynthesizer
- **Subgraphs**: SQL Agent subgraph with AST planner, validators, and executor
- **State Management**: Shared `GraphState` for auditability and reproducibility

### **Schema Management (`schema/`)**
- **Schema Store**: Persistent storage for schema snapshots with versioning
- **Schema Contracts**: Typed representations of database schemas
- **Versioning**: Multiple schema versions with eviction policies

### **Indexing System (`indexing/`)**
- **Schema Indexing**: Vector-based indexing of schema information
- **Chunk Builder**: Breaks schema into searchable chunks
- **Enrichment Service**: Enhances schema with example questions

### **Data Sources (`datasources/`)**
- **Registry**: Dynamic registration and management of database adapters
- **Protocols**: Standardized interfaces for database connectivity
- **Discovery**: Automatic discovery of available adapter types

### **LLM Management (`llm/`)**
- **Registry**: Management of multiple LLM instances
- **Configuration**: Flexible LLM provider configuration (OpenAI, etc.)
- **Routing**: Intelligent routing to appropriate LLMs

### **Authentication & Authorization (`auth/`)**
- **RBAC**: Role-based access control for data access
- **User Context**: Identity and permission context propagation
- **Policy Engine**: Fine-grained access control rules

## 🚀 Public API

The main public interface is provided through the `NL2SQL` class:

```python
from nl2sql import NL2SQL

# Initialize the engine
engine = NL2SQL(
    ds_config_path="configs/datasources.yaml",
    llm_config_path="configs/llm.yaml",
    policies_path="configs/policies.json"
)

# Run a natural language query
result = engine.run_query("Show top 10 customers by revenue")
print(result.final_answer)
```

### Two-Tier API Architecture

NL2SQL provides a two-tier API architecture:

#### 1. Core API (Python) - This Package
- **Interface**: Direct Python class interface (`NL2SQL` class)
- **Use Case**: Direct Python integration, embedded applications
- **Access**: Import and use directly in Python code

#### 2. REST API (HTTP)
- **Package**: API package (`nl2sql-api`)
- **Interface**: HTTP REST endpoints
- **Use Case**: Remote clients, web applications, TypeScript CLI
- **Access**: HTTP requests to API endpoints

Both APIs provide access to the same underlying NL2SQL engine functionality, allowing flexible integration options.

### Modular API Structure

The engine provides modular APIs for different functionality areas:

- `engine.query` - Query execution API (`run_query`, etc.)
- `engine.datasource` - Datasource management API (`add_datasource`, `list_datasources`, etc.)
- `engine.llm` - LLM configuration API (`configure_llm`, etc.)
- `engine.indexing` - Schema indexing API (`index_datasource`, `clear_index`, etc.)
- `engine.auth` - Authentication and RBAC API (`check_permissions`, `get_allowed_resources`, etc.)
- `engine.settings` - Configuration and settings API (`get_current_settings`, `validate_configuration`, etc.)
- `engine.results` - Result management API (`store_query_result`, `retrieve_query_result`, etc.)

For complete Core API documentation, see `docs/api/core.md` in this repo
or the API section of the published MkDocs site.

## 📋 Public API Classes

The public API exports the following classes and types:

- `NL2SQL` - Main engine class
- `QueryResult` - Query result container
- `UserContext` - User authentication context
- `ErrorSeverity`, `ErrorCode`, `PipelineError` - Error handling types
- `QueryAPI`, `DatasourceAPI`, `LLM_API`, `IndexingAPI`, `AuthAPI`, `SettingsAPI`, `ResultAPI` - Modular API classes

## 📦 Installation

```bash
# Install core only
pip install nl2sql-core

# Install core with selected adapters
pip install nl2sql-core[mysql,mssql]

# Install core with all adapters
pip install nl2sql-core[all]
```

## 🔖 Versioning Policy

All NL2SQL packages in this monorepo share a single version number and are
released together. Core, adapters, API, and CLI pin internal dependencies to
the same version to prevent mismatches.

## 🚀 Usage (CLI)

The core package exposes the CLI entry point:

```bash
python -m nl2sql.cli --query "Show me all users" --id my_postgres_db
```

## 🛡️ Architectural Invariants

| Invariant | Rationale | Mechanism |
| :--- | :--- | :--- |
| **No Unvalidated SQL** | Prevent hallucinations & data leaks | All plans pass through `LogicalValidator` (AST). `PhysicalValidator` exists but is not wired into the default SQL subgraph. |
| **Zero Shared State** | Crash Safety | Execution happens in isolated processes; no shared memory with the Control Plane. |
| **Fail-Fast** | Reliability | Circuit Breakers and Strict Timeouts prevent cascading failures (Retry Storms). |
| **Determinism** | Debuggability | Temperature-0 generation + Strict Typing (Pydantic) for all LLM outputs. |

## 🏗️ Pipeline Flow

The main execution flow follows this sequence:

1. **Datasource Resolver** → **Decomposer** → **Global Planner** → **Layer Router**
2. **SQL Agent Subgraph**: Schema Retriever → AST Planner → Logical Validator → Generator → Executor
3. **Self-correction loops**: When validation fails, the system refines and retries

### SQL Agent Subgraph Details:
- **AST Planner**: Generates Abstract Syntax Tree instead of direct SQL
- **Logical Validator**: Enforces RBAC and schema constraints
- **Generator**: Converts AST to dialect-specific SQL
- **Executor**: Runs SQL in sandboxed environment
- **Refiner**: Self-correction when validation fails

## 🔐 Security Features

- **RBAC System**: Role-based access control for data access
- **Schema Validation**: All queries validated against schema before execution
- **Sandboxed Execution**: SQL runs in isolated processes
- **Query Limiting**: Row limits, timeout controls, and byte limits
- **Audit Logging**: Comprehensive logging of all operations

## 📊 Observability

- **OpenTelemetry Integration**: Native support for distributed tracing
- **Metrics Collection**: Performance and operational metrics
- **Audit Logs**: Persistent forensic logs for compliance
- **Structured Logging**: Rich, contextual log information

## 📁 Directory Structure

```
src/nl2sql/
├── api/                  # Public API modules
│   ├── query_api.py      # Query execution API
│   ├── datasource_api.py # Datasource management API
│   ├── llm_api.py       # LLM configuration API
│   ├── indexing_api.py  # Schema indexing API
│   ├── auth_api.py      # Authentication API
│   ├── settings_api.py  # Settings API
│   └── result_api.py    # Result management API
├── auth/                # Authentication and RBAC
├── common/              # Common utilities and settings
├── configs/             # Configuration management
├── datasources/         # Datasource management and adapters
├── execution/           # Execution engine and artifacts
├── indexing/            # Schema indexing system
├── llm/                 # LLM management
├── pipeline/            # Graph orchestration
│   ├── nodes/           # Individual pipeline nodes
│   ├── subgraphs/       # Subgraph definitions
│   └── routes/          # Routing logic
├── schema/              # Schema management
├── secrets/             # Secret management
└── context.py           # Application context
└── public_api.py        # Public API facade
```

## 📋 Configuration

The engine requires configuration files for:
- `configs/datasources.yaml` - Database connection configurations
- `configs/llm.yaml` - LLM provider configurations  
- `configs/secrets.yaml` - Secret management configurations
- `configs/policies.json` - RBAC policies and permissions