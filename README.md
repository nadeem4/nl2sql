# NL2SQL

**NL2SQL** is an enterprise-grade, agentic Natural Language to SQL pipeline built on **LangGraph**.

Unlike simple prompt-to-query solutions, this project employs a **multi-agent supervisor architecture** to handle complex, real-world database interactions. It features dynamic routing across multiple database engines (Postgres, MySQL, MSSQL, SQLite), vector-based schema retrieval for scalability, and a rigorous **Plan-Validate-Execute** loop to ensure query correctness and safety.

Designed for observability and reliability, it provides detailed performance metrics, step-by-step reasoning logs, and deterministic SQL generation, making it suitable for production environments where accuracy is paramount.

## Key Features

- **Supervisor Architecture**: Dynamic routing of queries to the appropriate database using a 3-layer routing strategy (Vector, Multi-Query, LLM).
- **Cross-Database Support**: Map-Reduce architecture to handle complex queries spanning multiple databases (e.g., "Compare sales from MSSQL and inventory from MySQL").
- **LangGraph Pipeline**: Modular, stateful graph architecture with specialized agents for Intent, Planning, and Validation.
- **Multi-Database Support**: Seamlessly query across Postgres, MySQL, MSSQL, and SQLite.
- **Rule-Based SQL Generation**: Deterministic, token-efficient SQL generation using `sqlglot` to prevent syntax hallucinations.
- **Robust Validation Loop**: Pre-execution validation of plans against the schema to catch errors early.
- **Observability**: Real-time streaming of agent reasoning steps and structured JSON logging.
- **Vector Search (RAG)**: Scalable schema retrieval for large databases.

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
- `--show-perf`: Display detailed performance metrics (latency, tokens).
- `--vector-store <PATH>`: Use vector search for schema selection (requires indexing).
- `--id <ID>`: **Optional**. Force a specific datasource, bypassing the router (e.g., `manufacturing_ops`).
- `--no-exec`: Generate and validate SQL without executing it.
- `--json-logs`: Enable structured JSON logging.
- `--debug`: Enable debug logging for verbose output.
- `--visualize`: Visualize the execution trace and save it as a PNG image.

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

#### 1. Postgres (Operations)

```bash
python -m src.nl2sql.cli --id manufacturing_ops --query "List 5 machines with their serial numbers"
```

#### 2. MySQL (Supply Chain)

```bash
python -m src.nl2sql.cli --id manufacturing_supply --query "Show me top 3 products by price"
```

#### 3. MSSQL (History)

```bash
python -m src.nl2sql.cli --id manufacturing_history --query "Count total production runs"
```

#### 4. SQLite (Reference)

```bash
python -m src.nl2sql.cli --id manufacturing_ref --query "List all factories and their locations"
```

### 5. Cross-Database Query (Map-Reduce)

The system can automatically decompose complex queries into sub-queries, execute them in parallel, and aggregate the results.

#### Example 1: Sales vs Inventory (MSSQL + MySQL)

```bash
python -m src.nl2sql.cli --query "Compare sales from manufacturing_history and inventory from manufacturing_supply"
```

#### Example 2: Production vs Maintenance (MSSQL + Postgres)

```bash
python -m src.nl2sql.cli --query "Show production runs from manufacturing_history and maintenance logs from manufacturing_ops for 'Machine-1'"
```

#### Example 3: Employees & Locations (Postgres + SQLite)

```bash
python -m src.nl2sql.cli --query "List all employees from manufacturing_ops and their factory locations from manufacturing_ref"
```

**Expected Output:** A consolidated answer merging data from the respective databases.

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
- **Graph Visualization**: Use `--visualize` to generate a visual graph of the execution trace.

---

## Architecture

### The Pipeline (LangGraph)

We use a **Supervisor Architecture** where the graph itself manages routing and state.

```mermaid
flowchart TD
  user["User Query"] --> decomposer["Decomposer (AI)"]
  decomposer -- Single DB --> router["Router (AI)"]
  decomposer -- Multi DB --> split["Parallel Execution"]
  
  subgraph Execution Pipeline
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
  end
  
  split --> router
  executor --> aggregator["Aggregator (AI)"]
  aggregator --> answer["Final Answer"]
  
  style user fill:#f6f8fa,stroke:#aaa
  style decomposer fill:#e1f5fe,stroke:#01579b
  style router fill:#e1f5fe,stroke:#01579b
  style aggregator fill:#e1f5fe,stroke:#01579b
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
- **SQL Generator (Code)**: Deterministically compiles the plan to SQL using `sqlglot` (0 tokens).
- **Executor (Code)**: Runs the SQL (read-only) and returns results.

### Performance Breakdown

The CLI provides a detailed breakdown of time and token usage, including a top-level matrix and per-datasource details:

```text
Performance & Metrics
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ Top Level Performance                                                                    │
│ ┏━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓ │
│ ┃ Metric      ┃ Decomposer ┃ Aggregator ┃ Exec (manufacturing_ops) ┃ Total             ┃ │
│ ┡━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩ │
│ │ Latency (s) │     0.0000 │     0.0000 │                   7.2700 │            7.2700 │ │
│ │ Token Usage │          0 │          0 │                     3029 │              3029 │ │
│ └─────────────┴────────────┴────────────┴──────────────────────────┴───────────────────┘ │
│                                                                                          │
│ Performance: manufacturing_ops                                                           │
│ ┏━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━┓                            │
│ ┃ Node      ┃   Type   ┃    Model    ┃ Latency (s) ┃ Tokens ┃                            │
│ ┡━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━┩                            │
│ │ Intent    │    AI    │ gpt-4o-mini │      1.9300 │    569 │                            │
│ │ Planner   │    AI    │ gpt-4o-mini │      4.9900 │   2456 │                            │
│ │ Generator │  Non-AI  │      -      │      0.0000 │      - │                            │
│ │ Executor  │  Non-AI  │      -      │      0.3500 │      - │                            │
│ └───────────┴──────────┴─────────────┴─────────────┴────────┘                            │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```

### Project Structure

- `src/`: Core modules (`nodes`, `langgraph_pipeline`, `datasource_config`, `llm_registry`).
- `configs/`: YAML configurations for datasources and LLMs.
- `scripts/`: Utilities (e.g., `seed_databases.py`).
- `tests/`: Unit and integration tests.

---

## Evaluation Framework

We employ a rigorous evaluation framework to ensure the pipeline's reliability across different complexity levels.

### 1. The Golden Dataset (`tests/golden_dataset.yaml`)

A curated suite of 20 test cases designed to stress-test the **Router** and **SQL Generation**.

| Tier | Complexity | Goal | Target Layer |
| :--- | :--- | :--- | :--- |
| **Easy** | Keyword Match | Test speed & exact retrieval | Layer 1 (Vector) |
| **Medium** | Ambiguous/Slang | Test enrichment & multi-query | Layer 2 (Multi-Query) |
| **Hard** | Reasoning Required | Test intent understanding | Layer 3 (LLM) |

### 2. Metrics Measured

The benchmark tool captures:

- **Routing Accuracy**: % of queries routed to the correct datasource.
- **Execution Accuracy**: % of generated SQL returning the correct rows (compared to ground truth).
- **Latency**: End-to-end time (aiming for < 1s for L1, < 3s for L2).
- **Token Usage**: Cost efficiency (aiming for 0 tokens for L1 cache hits).

### 3. Running Benchmarks

**A. Routing-Only Evaluation** (Fast)
Test just the router's ability to pick the right DB.

```bash
python -m src.nl2sql.cli --benchmark --dataset tests/golden_dataset.yaml --routing-only
```

**B. End-to-End Evaluation** (Slow)
Test routing + SQL generation + execution correctness.

```bash
python -m src.nl2sql.cli --benchmark --dataset tests/golden_dataset.yaml
```

**C. Regression Testing**
Use the stub LLM to test the harness itself without API costs.

```bash
python -m src.nl2sql.cli --benchmark --dataset tests/golden_dataset.yaml --stub-llm
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
