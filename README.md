# NL2SQL Platform

An enterprise-grade **Natural Language to SQL** engine built on an Agentic Graph Architecture.

## 🚀 Overview

This platform transforms complex natural language questions into safe, optimized, and executable SQL queries across multiple database engines (PostgreSQL, MySQL, MSSQL, SQLite). It uses a **Directed Cyclic Graph** (LangGraph) to orchestrate planning, validation, generation, and self-correction.

### Key Features

* **🛡️ Security First**: Strict AST Validation, RBAC Policies, and Read-Only enforcement.
* **🧠 Agentic Reasoning**: Self-correcting nodes that fix SQL errors automatically.
* **🔌 Polyglot**: First-class support for Postgres, MySQL, MSSQL, and SQLite.
* **⚡ Smart Routing**: Decomposes complex queries into sub-queries for multi-datasource environments.

## 🏁 Quick Demo

Explore the platform's capabilities with our interactive setup wizard. You can choose between **Lite Mode** (in-memory, no deps) or **Docker Mode** (real databases).

### 1. Lite Mode (Fastest) uses SQLite

Perfect for a standardized, local environment without needing Docker.

```bash
nl2sql setup --demo
```

### 2. Docker Mode (Full Fidelity) uses Postgres

Spins up real orchestrator and database containers for a production-like test.

```bash
nl2sql setup --demo --docker
```

## 🛠️ Installation

This is a monorepo. To develop or run the platform from source:

### Prerequisites

* Python 3.10+
* Docker & Docker Compose (optional, for Integration Tests)

### Setup

1. **Clone and Install**:

    ```bash
    git clone https://github.com/nadeem4/nl2sql.git
    cd nl2sql
    
    # Create virtual environment
    python -m venv venv
    source venv/bin/activate  # or .\venv\Scripts\activate on Windows
    
    # Install Core and CLI
    pip install -e packages/core
    pip install -e packages/adapter-sdk
    pip install -e packages/cli
    pip install -e packages/adapters/postgres  # Install specific adapters as needed
    ```

2. **Verify Installation**:

    ```bash
    nl2sql --help
    ```

## 🏗️ Architecture

The system is composed of specialized Neural Nodes:

1. **Semantic Analysis**: Intent classification and entity extraction.
2. **Decomposer (Router)**: Splits complex queries and routes them to the correct datasource.
3. **Planner**: Generates a database-agnostic Abstract Syntax Tree (AST).
4. **Validator**: Enforces security policies and logical correctness on the AST.
5. **Generator**: Compiles AST to dialect-specific SQL.
6. **Executor**: Runs the query in a sandboxed environment.
7. **Refiner**: Self-corrects errors by analyzing stack traces and feedback.
8. **Aggregator**: Synthesizes results from multiple sub-queries.

See [Architecture Documentation](docs/core/architecture.md) for details.

## 📚 Documentation

Full documentation is available in the `docs/` directory.

```bash
pip install -r requirements-docs.txt
mkdocs serve
```

## 📂 Repository Structure

* `packages/core`: The core graph engine, nodes, and state management.
* `packages/cli`: Command-line interface tool.
* `packages/adapter-sdk`: SDK for building custom database adapters.
* `configs/`: Configuration files (Policies, Datasources).
* `docs/`: MkDocs source files.
