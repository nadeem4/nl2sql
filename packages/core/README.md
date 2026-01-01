# NL2SQL Core

The **NL2SQL Core** (`nl2sql-core`) is the brain of the natural language to SQL engine. It orchestrates the entire query lifecycle using a graph-based agent architecture.

## 🧠 Key Components

* **LangGraph Pipeline**: A state machine that manages the flow between Intent Analysis, Decomposition, Schema Retrieval, and SQL Generation.
* **Orchestrator Vector Store**: A dual-index vector database (ChromaDB) that stores both Table Schemas and Few-Shot Examples for accurate retrieval.
* **Plugin System**: Dynamically loads database adapters at runtime via `importlib`.

## 📦 Installation

```bash
pip install -e packages/core
```

## 🚀 Usage (CLI)

The core package exposes the CLI entry point:

```bash
python -m nl2sql.cli --query "Show me all users" --id my_postgres_db
```

## 🏗️ Architecture

The pipeline consists of the following nodes:

1. **Semantic Analysis Node**: Entry point. Canonicalizes query and expands synonyms for better grounding.
2. **Decomposer Node**: Deconstructs complex queries using the enriched semantic context.
3. **Planner Node**: Hydrates schema for relevant tables and generates the SQL execution plan.
4. **Generator Node**: Writes dialect-specific SQL (T-SQL, PL/pgSQL, etc.).
5. **Executor Node**: Executes the query via the adapter.
6. **Validator Node**: Ensures safety and compliance against the schema.
