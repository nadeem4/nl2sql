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

1. **Intent Node**: Classifies query (Tabular, Plot, Generic).
2. **Decomposer Node**: Splits complex queries or identifies required tables.
3. **Schema Node**: Retrieves the exact table schema from the adapter.
4. **Generator Node**: Writes the SQL query.
5. **Executor Node**: executing the query via the adapter.
