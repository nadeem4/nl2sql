# NL2SQL

This project implements a LangGraph-based NL→SQL pipeline with pluggable LLMs and multi-engine support. It ships with a SQLite manufacturing dataset, structured planner/generator outputs, guardrails, and a CLI for interactive queries.

## Features

- **LangGraph Pipeline**: Intent → Schema → Planner → SQL Generator → Validator → Executor.
- **Multi-Database**: Supports Postgres, MySQL, MSSQL, and SQLite via Docker.
- **Structured Outputs**: Pydantic parsers reject wildcards and enforce limits/order.
- **Token Efficient**: Rule-based SQL generation (sqlglot) and logic-based validation minimize LLM costs.
- **Observability**: CLI flags to stream reasoning steps and structured JSON logs.

---

## Quick Start

Follow this guide to set up the environment and run your first query.

### Prerequisites

- **Python 3.10+**
- **Docker & Docker Compose**
- **OpenAI API Key**

### 1. Installation

```bash
git clone https://github.com/nadeem4/nl2sql.git
cd nl2sql
pip install -r requirements.txt
```

### 2. Infrastructure Setup

Start the database containers (Postgres, MySQL, MSSQL) and seed them with synthetic manufacturing data:

```bash
# Start containers
docker-compose up -d

# Seed data (wait ~10s for DBs to initialize)
python scripts/seed_databases.py --wait
```

### 3. Configuration

Create a `.env` file in the root directory:

```bash
OPENAI_API_KEY="sk-..."
```

### 4. Verify Setup

Run a query against the **Postgres** database to verify the pipeline:

```bash
python -m src.nl2sql.cli --id manufacturing_ops --query "List 5 machines"
```

**Expected Output:** A structured log of the AI's reasoning followed by a table of 5 machines.

---

## Configuration

### Datasources (`configs/datasources.yaml`)

Define connection strings and settings for each database.

```yaml
manufacturing_sqlite:
  engine: sqlite
  connection_string: sqlite:///data/manufacturing.db
  tables: [] # Empty list = include all

manufacturing_ops:
  engine: postgres
  connection_string: postgresql+psycopg2://user:password@localhost:5432/manufacturing_ops
```

### LLM Settings (`configs/llm.yaml`)

Map specific agents to different LLM providers/models.

```yaml
default:
  provider: openai
  model: gpt-4o

agents:
  planner:
    provider: openai
    model: gpt-4o-mini # Use a cheaper model for planning
```

### Routing Examples (`configs/sample_questions.yaml`)

Map datasource IDs to lists of sample questions to improve routing accuracy (Layer 1).

```yaml
manufacturing_ops:
  - "List all machines"
  - "Which machines are offline?"
```

```yaml
manufacturing_supply:
  - "Show top products by price"
  - "List all products"
```

---

## Usage Guide

### CLI Basics

The CLI (`src.nl2sql.cli`) is the main entry point. It uses the **Router Node** to automatically select the correct datasource.

- `--query "..."`: The natural language question.
- `--show-thoughts`: Display step-by-step AI reasoning.
- `--vector-store <PATH>`: Use vector search for schema selection (requires indexing).
- `--id <ID>`: **Optional**. Force a specific datasource, bypassing the router (e.g., `manufacturing_ops`).

### Multi-Database Support

The system simulates a manufacturing enterprise distributed across 4 databases:

| ID | Engine | Content | Example Query |
|---|---|---|---|
| `manufacturing_ops` | **Postgres** | Machines, Maintenance, Employees | `List 5 machines with their serial numbers` |
| `manufacturing_supply` | **MySQL** | Products, Inventory, Suppliers | `Show me top 3 products by price` |
| `manufacturing_history` | **MSSQL** | Production Runs, Sales, Defects | `Count total production runs` |
| `manufacturing_ref` | **SQLite** | Factories, Shifts, Machine Types | `List all factories and their locations` |

### Sample Commands

Run these commands to test each database:

**1. Postgres (Operations)**

```bash
python -m src.nl2sql.cli --id manufacturing_ops --query "List 5 machines with their serial numbers"
```

**2. MySQL (Supply Chain)**

```bash
python -m src.nl2sql.cli --id manufacturing_supply --query "Show me top 3 products by price"
```

**3. MSSQL (History)**

```bash
python -m src.nl2sql.cli --id manufacturing_history --query "Count total production runs"
```

**4. SQLite (Reference)**

```bash
python -m src.nl2sql.cli --id manufacturing_ref --query "List all factories and their locations"
```

### Vector Search (RAG)

For large schemas, use vector search to dynamically select relevant tables.

1. **Index the Schema**:

    ```bash
    python -m src.nl2sql.cli --index --vector-store ./chroma_db
    ```

2. **Query with Context**:

    ```bash
    python -m src.nl2sql.cli --query "Show top 5 products" --vector-store ./chroma_db
    ```

### Observability & Logging

- **Stream Reasoning**: Use `--show-thoughts` to see the Intent, Planner, and Generator steps.
- **JSON Logs**: Use `--json-logs` for structured output suitable for log ingestion.
- **Debug Mode**: Use `--debug` for verbose output.

---

## Architecture

### The Pipeline (LangGraph)

We use a **Supervisor Architecture** where the graph itself manages routing and state.

```mermaid
flowchart TD
  user["User Query"] --> router["Router (AI)"]
  router --> intent["Intention (AI)"]
  intent --> schema["Schema (non-AI)"]
  schema --> planning["Planning Subgraph"]
  
  subgraph Planning Subgraph
    planner["Planner (AI)"] --> validator["Validator (non-AI)"]
    validator -- Invalid --> summarizer["Summarizer (AI)"]
    summarizer --> planner
  end
  
  validator -- Valid --> generator["SQL Generator (non-AI)"]
  generator --> executor["Executor (non-AI)"]
  executor --> answer["Answer/Result Sample"]
  
  style user fill:#f6f8fa,stroke:#aaa
  style router fill:#e1f5fe,stroke:#01579b
  style answer fill:#f6f8fa,stroke:#aaa
```

### Vectorization Strategy

To support efficient querying across large or multiple databases, we use a two-tiered vectorization approach:

1. **Datasource Routing**:
    - **What**: Indexes the `description` of each database.
    - **Why**: Determines *which* database contains the relevant data (e.g., "Sales" vs. "Inventory").
    - **Strategy**:
        - **Layer 1 (Fast)**: Vector search against database descriptions and 200+ sample questions.
        - **Layer 2 (Robust)**: If confidence is low (distance > 0.4), an LLM generates 3 query variations and votes on the best datasource.
        - **Layer 3 (Reasoning)**: If Layer 2 fails or remains uncertain, a dedicated LLM Agent analyzes the schema definitions to make a final decision.

2. **Schema Selection**:
    - **What**: Indexes table metadata (columns, foreign keys, comments).
    - **Why**: Determines *which tables* are needed for the query within the selected database.

This allows the system to scale to hundreds of tables without overwhelming the LLM's context window.

### Core Agents

- **Intent (AI)**: Classifies query type and extracts entities.
- **Planner (AI)**: Generates a database-agnostic structured plan (tables, joins, filters).
- **Validator (Code)**: Verifies the plan against the schema (column existence, types).
- **Validator (Code)**: Verifies the plan against the schema (column existence, types).
- **SQL Generator (Code)**: Deterministically compiles the plan to SQL using `sqlglot` (0 tokens).
- **Executor (Code)**: Runs the SQL (read-only) and returns results.

### Performance Breakdown

The CLI provides a detailed breakdown of time and token usage per node:

```text
Performance:
Node      | Type   | Model                  | Tokens | Time 
----------+--------+------------------------+--------+------
Router    | AI     | text-embedding-3-small | 4      | 1.45s
Intent    | AI     | gpt-4o-mini            | 569    | 1.93s
Schema    | Non-AI | -                      | -      | 0.35s
Planner   | AI     | gpt-4o-mini            | 2456   | 4.99s
Generator | Non-AI | -                      | -      | 0.00s
Validator | Non-AI | -                      | -      | 0.00s
Executor  | Non-AI | -                      | -      | 0.00s
TOTAL     | -      | -                      | 3029   | 8.73s
```

### Project Structure

- `src/`: Core modules (`nodes`, `langgraph_pipeline`, `datasource_config`, `llm_registry`).
- `configs/`: YAML configurations for datasources and LLMs.
- `scripts/`: Utilities (e.g., `seed_databases.py`).
- `tests/`: Unit and integration tests.

---

## Benchmarking

Compare performance (latency, success rate, tokens) across LLMs.

1. **Configure**: Create `configs/benchmark_suite.yaml`.
2. **Run**:

    ```bash
    python -m src.nl2sql.cli --query "List Products" --benchmark --bench-config configs/benchmark_suite.yaml --iterations 3
    ```

## Development

### Running Tests

Run the test suite using pytest:

```bash
python -m pytest tests/
```

### Adding New Engines

1. Add the driver to `requirements.txt`.
2. Add a connection profile to `configs/datasources.yaml`.
3. (Optional) Add specific DDL/Seeding logic to `scripts/seed_databases.py`.
