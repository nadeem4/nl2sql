# NL2SQL

**NL2SQL** is an enterprise-grade, agentic Natural Language to SQL pipeline built on **LangGraph**.

Unlike simple prompt-to-query solutions, this project employs a **Map-Reduce Supervisor Architecture** to handle complex, real-world database interactions. It features dynamic routing across multiple database engines (Postgres, MySQL, MSSQL, SQLite), vector-based schema retrieval for scalability, and a rigorous **Plan-Validate-Execute** loop to ensure query correctness and safety.

Designed for observability and reliability, it provides detailed performance metrics, step-by-step reasoning logs, and deterministic SQL generation, making it suitable for production environments where accuracy is paramount.

## Key Features

- **Supervisor Architecture**: Centralized dynamic routing using a 3-layer strategy (Vector, Multi-Query, LLM) to dispatch tasks.
- **Map-Reduce Strategy**: Handles complex cross-database queries by decomposing them into parallel sub-tasks and aggregating results.
- **LangGraph Pipeline**: Modular, stateful graph architecture with specialized agents for Intent, Planning, and Validation.
- **Multi-Database Support**: Seamlessly query across Postgres, MySQL, MSSQL, and SQLite.
- **Rule-Based SQL Generation**: Deterministic, token-efficient SQL generation using `sqlglot` to prevent syntax hallucinations.
- **Robust Validation Loop**: Pre-execution validation of plans against the schema to catch errors early.
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

The CLI (`src.nl2sql.cli`) is the main entry point. It uses the **Router Node** to automatically select the correct datasource.

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

**Sample Output:**

<details>
<summary><b>Click to see Sample Output</b></summary>

> **Summary**
> The query successfully retrieved a list of 5 machines along with their serial numbers.

<details>
<summary><b>Execution Trace</b></summary>
<br>

- **Router**: Layer 1 Distance 0.25 (Matched "List all machines")
- **Intent**: Classification: READ, Keywords: machines, serial numbers
- **Planner**: Plan: Select name, serial_number from machines limit 5
- **Generator**: SELECT name, serial_number FROM machines LIMIT 5
- **Executor**: Executed SQL on manufacturing_ops. Rows returned: 5.

</details>

**Data**

| Machine Name | Serial Number |
| :--- | :--- |
| Machine-0 | 54402ab7-f674-4856-8129-3bd0b82c0171 |
| Machine-1 | 46eb37fc-f2c2-4c76-809c-a7d94e8c0605 |
| Machine-2 | 736e945f-f356-4d2f-abf3-541bbf5ccb3c |
| Machine-3 | a1b2c3d4-e5f6-7890-1234-56789abcdef0 |
| Machine-4 | b2c3d4e5-f6a7-8901-2345-67890bcdef12 |

**Datasource Used:** `manufacturing_ops`
</details>

#### 2. MySQL (Supply Chain)

```bash
python -m src.nl2sql.cli --id manufacturing_supply --query "Show me top 3 products by price"
```

**Sample Output:**

<details>
<summary><b>Click to see Sample Output</b></summary>

> **Summary**
> The top 3 products by price have been successfully retrieved.

<details>
<summary><b>Execution Trace</b></summary>
<br>

- **Router**: Layer 1 Distance 0.32 (Matched "Show top products by price")
- **Intent**: Classification: READ, Keywords: products, price
- **Planner**: Plan: Select name, sku, price from products order by price desc limit 3
- **Generator**: SELECT name, sku, unit_price FROM products ORDER BY unit_price DESC LIMIT 3
- **Executor**: Executed SQL on manufacturing_supply. Rows returned: 3.

</details>

**Data**

| Product ID | SKU | Product Name | Price |
| :--- | :--- | :--- | :--- |
| 30 | 3026430016128 | Harness Cross-Media Relationships | 10.91 |
| 117 | 9624973332824 | Enable Vertical Bandwidth | 12.80 |
| 339 | 5303716685598 | Deploy Magnetic Channels | 16.39 |

**Datasource Used:** `manufacturing_supply`
</details>

#### 3. MSSQL (History)

```bash
python -m src.nl2sql.cli --id manufacturing_history --query "Count total production runs"
```

**Sample Output:**

<details>
<summary><b>Click to see Sample Output</b></summary>

> **Summary**
> The total number of production runs is 5000.

<details>
<summary><b>Execution Trace</b></summary>
<br>

- **Router**: Layer 1 Distance 0.18 (Matched "Count total production runs")
- **Intent**: Classification: AGGREGATE, Keywords: count, production runs
- **Planner**: Plan: Select count(*) from production_runs
- **Generator**: SELECT COUNT(*) FROM production_runs
- **Executor**: Executed SQL on manufacturing_history. Rows returned: 1.

</details>

**Datasource Used:** `manufacturing_history`
</details>

#### 4. SQLite (Reference)

```bash
python -m src.nl2sql.cli --id manufacturing_ref --query "List all factories and their locations"
```

**Sample Output:**

<details>
<summary><b>Click to see Sample Output</b></summary>

> **Summary**
> The query retrieved 3 factories: Plant Austin (TX), Plant Berlin (DE), and Plant Tokyo (JP).

<details>
<summary><b>Execution Trace</b></summary>
<br>

- **Router**: Layer 1 Distance 0.22 (Matched "List all factories")
- **Intent**: Classification: READ, Keywords: factories, locations
- **Planner**: Plan: Select name, location from factories
- **Generator**: SELECT name, location FROM factories
- **Executor**: Executed SQL on manufacturing_ref. Rows returned: 3.

</details>

**Data**

| Factory Name | Location |
| :--- | :--- |
| Plant Austin | Austin, TX |
| Plant Berlin | Berlin, DE |
| Plant Tokyo | Tokyo, JP |

**Datasource Used:** `manufacturing_ref`
</details>

### 5. Cross-Database Query (Map-Reduce)

The system can automatically decompose complex queries into sub-queries, execute them in parallel, and aggregate the results.

#### Example 1: Sales vs Inventory (MSSQL + MySQL)

```bash
python -m src.nl2sql.cli --query "Compare sales from manufacturing_history and inventory from manufacturing_supply"
```

**Sample Output:**

<details>
<summary><b>Click to see Sample Output</b></summary>

> **Summary**
> The sales data shows total sales and quantities sold by customer, while the inventory data provides quantities on hand for various products in different locations. A comparison reveals potential stock issues for high-selling products.

<details>
<summary><b>Execution Trace</b></summary>
<br>

- **Decomposer**: Strategy: PARALLEL (Sales, Inventory) based on keyword overlap.
- **Branch 1 (History)**: "Get sales data" -> Router: MSSQL -> SQL: SELECT ... FROM sales_orders ...
- **Branch 2 (Supply)**: "Get inventory interactions" -> Router: MySQL -> SQL: SELECT ... FROM inventory ...
- **Aggregator**: Combined results from 2 branches into final comparison analysis.

</details>

**Data**

| Customer ID | Total Qty Sold | Total Sales | Product ID | Location | Qty on Hand |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 23 | 1035 | $915,361.36 | 326 | WH-C | 1 |
| 46 | 1692 | $1,696,801.35 | 295 | WH-C | 2180 |
| 69 | 2052 | $1,918,153.05 | 223 | WH-A | 3005 |

**Datasource Used:** `['manufacturing_history', 'manufacturing_supply']`
</details>

#### Example 2: Production vs Maintenance (MSSQL + Postgres)

```bash
python -m src.nl2sql.cli --query "Show production runs from manufacturing_history and maintenance logs from manufacturing_ops for 'Machine-1'"
```

**Sample Output:**

<details>
<summary><b>Click to see Sample Output</b></summary>

> **Summary**
> The production runs for 'Machine-1' show a total of 98 records with various quantities produced and scrap counts.

**Data**

| Product Run ID | Product ID | Machine ID | Start Time | End Time | Qty Produced | Scrap Count |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 19 | 447 | 1 | 2025-08-01 11:03:37 | 2025-08-01 12:03:37 | 4698 | 30 |
| 29 | 33 | 1 | 2025-05-15 04:13:56 | 2025-05-15 08:13:56 | 8386 | 41 |

**Datasource Used:** `['manufacturing_ops', 'manufacturing_history']`
</details>

#### Example 3: Employees & Locations (Postgres + SQLite)

```bash
python -m src.nl2sql.cli --query "List all employees from manufacturing_ops and their factory locations from manufacturing_ref"
```

**Sample Output:**

<details>
<summary><b>Click to see Sample Output</b></summary>

> **Summary**
> The combined data lists all employees from the manufacturing operations along with their corresponding factory locations.

**Data**

| Employee Name | Role | Factory ID | Location | Hired Date | Email |
| :--- | :--- | :--- | :--- | :--- | :--- |
| James Fitzpatrick | Technician | 1 | Austin, TX | 2024-09-19 | <masonkyle@example.com> |
| James Colon | Operator | 3 | Berlin, DE | 2023-07-04 | <sgarcia@example.com> |
| Brian Russell | Operator | 1 | Austin, TX | 2023-06-13 | <mlopez@example.com> |

**Datasource Used:** `['manufacturing_ops', 'manufacturing_ref']`
</details>

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

We use a hybrid **Supervisor + Map-Reduce Architecture**:

1. **Supervisor (The Brain)**: The `DecomposerNode` acts as the supervisor, analyzing every query to decide if it requires a single datasource or multiple.
2. **Map-Reduce (The Strategy)**: When the supervisor detects a complex query (e.g., "Compare X and Y"), it triggers a **Map-Reduce** flow:
    - **Map**: Fans out sub-queries to parallel execution branches.
    - **Reduce**: Aggregates results from all branches into a final answer.

For a deep dive into the Map-Reduce pattern, see [**docs/ARCHITECTURE_MAP_REDUCE.md**](docs/ARCHITECTURE_MAP_REDUCE.md).

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
        - **Layer 3 (Reasoning)**: If Layer 2 fails or remains uncertain, a dedicated LLM Agent analyzes the datasource descriptions to make a final decision.

2. **Schema Selection**:
    - **What**: Indexes table metadata (columns, foreign keys, comments).
    - **Why**: Determines *which tables* are needed for the query within the selected database.

This allows the system to scale to hundreds of tables without overwhelming the LLM's context window.

### Core Agents

- **Decomposer (Supervisor)**: The system brain. Analyzes queries to determine strategy (Single vs Multi-DB) and manages the Map-Reduce flow.
- **Router (AI)**: Selects the appropriate database based on vector/reasoning alignment.
- **Intent (AI)**: Classifies query type and extracts entities.
- **Planner (AI)**: Generates a database-agnostic structured plan (tables, joins, filters).
- **Validator (Code)**: Verifies the plan against the schema (column existence, types).
- **SQL Generator (Code)**: Deterministically compiles the plan to SQL using `sqlglot` (0 tokens).
- **Executor (Code)**: Runs the SQL (read-only) and returns results.
- **Aggregator (AI)**: Combines results from parallel sub-queries into a final natural language answer.

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
