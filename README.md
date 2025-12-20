# NL2SQL

**NL2SQL** is an enterprise-grade, agentic Natural Language to SQL pipeline built on **LangGraph**.

Unlike simple prompt-to-query solutions, this project employs a **Map-Reduce Supervisor Architecture** to handle complex, real-world database interactions. It features dynamic routing across multiple database engines (Postgres, MySQL, MSSQL, SQLite), vector-based schema retrieval for scalability, and a rigorous **Plan-Validate-Execute** loop to ensure query correctness and safety.

Designed for observability and reliability, it provides detailed performance metrics, step-by-step reasoning logs, and deterministic SQL generation, making it suitable for production environments where accuracy is paramount.

## Key Features

- **Intent-Driven Architecture**: Uses a specialized **Intent Node** to classify queries as `TABULAR`, `KPI` (Fast Path), or `SUMMARY` (Slow Path).
- **Hybrid Execution Lanes**:
  - **Fast Lane**: Zero-shot SQL generation for simple lookups (< 2s latency).
  - **Slow Lane**: Full Plan-Validate-Execute agentic loop for complex analysis.
- **Supervisor Architecture**: Centralized dynamic routing via `DecomposerNode` using vector search.
- **Map-Reduce Strategy**: Handles complex cross-database queries by decomposing them into parallel sub-tasks.
- **LangGraph Pipeline**: Modular, stateful graph architecture.
- **Multi-Database Support**: Seamlessly query across Postgres, MySQL, MSSQL, and SQLite.
- **Robust Validation Loop**: Pre-execution validation of plans (Slow Lane only).
- **Observability**: Real-time streaming of agent reasoning steps.
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

The CLI (`src.nl2sql.cli`) is the main entry point.

- `--query "..."`: The natural language question.
- `--verbose`: Display step-by-step AI reasoning.
- `--show-perf`: Display detailed performance metrics (latency, tokens).
- `--vector-store <PATH>`: Use vector search for schema selection (requires indexing).
- `--id <ID>`: **Optional**. Force a specific datasource, bypassing the router (e.g., `manufacturing_ops`).
- `--no-exec`: Generate and validate SQL without executing it.
- `--debug`: Enable debug logging for verbose output.
- `--include-ids <ID>...`: Benchmark only specific test case IDs.

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

- **Stream Reasoning**: Use `--verbose` to see the Intent, Planner, and Generator steps.
- **Debug Mode**: Use `--debug` for verbose output.

---

## Architecture

### The Pipeline (LangGraph)

We use a hybrid **Intent-Driven + Map-Reduce Architecture**:

1. **Intent & Routing**:
    - **Intent Node**: Classifies user goal and standardizes the query.
    - **Decomposer (Supervisor)**: Uses vector search to find the right data source(s). If multiple are needed, it triggers Map-Reduce.
2. **Execution Lanes**:
    - **Fast Lane**: Direct SQL generation (`DirectSQLNode`) for simple tabular data.
    - **Slow Lane**: Reasoning loop (`Planner` -> `Validator` -> `Generator`) for complex requests.
3. **Aggregation**:
    - The `AggregatorNode` stitches results together, using an LLM only if necessary (Summary Mode).

For a deep dive into the Map-Reduce pattern, see [**docs/ARCHITECTURE_MAP_REDUCE.md**](docs/ARCHITECTURE_MAP_REDUCE.md).

```mermaid
flowchart TD
  user["User Query"] --> intent["Intent Node"]
  intent --> decomposer["Decomposer (AI)"]
  decomposer -- Single DB --> schema
  decomposer -- Multi DB --> splits["Splits (Map)"]
  
  splits --> schema
  
  subgraph Execution Lane
    schema["Schema Node"] --> lane_logic{Fast or Slow?}
    lane_logic -- Fast --> directSQL["DirectSQL (AI)"]
    lane_logic -- Slow --> planner["Planner (AI)"]
    planner --> validator
    validator --> generator
    
    directSQL --> executor
    generator --> executor["Executor"]
  end
  
  executor --> aggregator["Aggregator"]
  aggregator --> answer["Final Answer"]
  
  style user fill:#f6f8fa,stroke:#aaa
  style decomposer fill:#e1f5fe,stroke:#01579b
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
        - **Layer 2 (Robust)**: Decomposer LLM uses retrieved context to form sub-queries.

2. **Schema Selection**:
    - **What**: Indexes table metadata (columns, foreign keys, comments).
    - **Why**: Determines *which tables* are needed for the query within the selected database.

This allows the system to scale to hundreds of tables without overwhelming the LLM's context window.

### Core Agents

- **Intent (AI)**: Entry point. Classifies intent (Table vs Summary) and standardizes query.
- **Decomposer (Supervisor)**: Orchestrates routing and query splitting.
- **DirectSQL (AI)**: **Fast Lane**. Generates SQL for simple queries without planning.
- **Planner (AI)**: **Slow Lane**. Generates a structured plan.
- **Validator (Code)**: **Slow Lane**. Verifies the plan against schema.
- **Generator (Code)**: Compiles plan to SQL.
- **Executor (Code)**: Runs the SQL.
- **Aggregator (AI)**: Synthesizes final results. Skips AI for simple table outputs.

### Performance Breakdown

The CLI provides a detailed breakdown of time and token usage, including a top-level matrix and per-datasource details:

Enable this detailed view by adding the `--show-perf` flag.

<details>
<summary><b>Click to see Sample Performance Output</b></summary>

**Top Level Performance**

| Metric | Decomposer | Aggregator | Exec (manufacturing_ops) | Total |
| :--- | :--- | :--- | :--- | :--- |
| Latency (s) | 0.0000 | 0.0000 | 7.2700 | 7.2700 |
| Token Usage | 0 | 0 | 3029 | 3029 |

**Performance: manufacturing_ops**

| Node | Type | Model | Latency (s) | Tokens |
| :--- | :--- | :--- | :--- | :--- |
| Intent | AI | gpt-4o-mini | 1.9300 | 569 |
| Planner | AI | gpt-4o-mini | 4.9900 | 2456 |
| Generator | Non-AI | - | 0.0000 | - |
| Executor | Non-AI | - | 0.3500 | - |

</details>

### Project Structure

- `src/`: Core modules (`nodes`, `langgraph_pipeline`, `datasource_config`, `llm_registry`).
- `configs/`: YAML configurations for datasources and LLMs.
- `scripts/`: Utilities (e.g., `seed_databases.py`).
- `tests/`: Unit and integration tests.

---

## Evaluation Framework

We employ a rigorous evaluation framework to ensure the pipeline's correctness and stability.

For detailed documentation on metrics, architecture, and usage, see [**EVALUATION.md**](EVALUATION.md).

### Key Metrics

- **Execution Accuracy**: Verifies that generated SQL returns the correct data (not just string matching).
- **Stability (Pass@K)**: Measures pipeline reliability and routing consistency over multiple iterations.
- **Routing Accuracy**: Tracks the performance of the 3-layer routing system.

### Quick Benchmark

Run the full test suite with parallel execution:

```bash
python -m src.nl2sql.cli --benchmark --dataset tests/golden_dataset.yaml
```

To test stability (run each question 5 times):

```bash
python -m src.nl2sql.cli --benchmark --dataset tests/golden_dataset.yaml --iterations 5
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
