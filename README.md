# NL2SQL

This project implements a LangGraph-based NL→SQL pipeline with pluggable LLMs and multi-engine support. It ships with a SQLite manufacturing dataset, structured planner/generator outputs, guardrails, and a CLI for interactive queries.

## Features
- LangGraph pipeline: intent → schema → planner → SQL generator → validator → executor.
- Structured outputs with Pydantic parsers; rejects wildcards and enforces limits/order when specified.
- Datasource profiles via SQLAlchemy (SQLite starter; Postgres profile example included).
- LLM registry with per-agent configs (OpenAI via LangChain) and `.env` support for keys.
- Guardrails: read-only, limit clamp, wildcard expansion, UNION/multi-statement blocking, ORDER BY enforcement.
- CLI with formatted output and optional stub LLM for offline testing.

## Setup
1) Install dependencies:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
2) Create the SQLite demo DB (already created if you ran the script):
   ```bash
   python scripts/setup_sqlite_manufacturing.py --db data/manufacturing.db
   ```
3) Set your OpenAI key (or add to `.env`):
   ```bash
   # Create a .env file
   OPENAI_API_KEY="sk-..."
   ```
4) Optional: install the package (for `nl2sql-cli`):
   ```bash
   pip install -e .
   ```

## Running the CLI
- From source:
  ```bash
  python -m src.nl2sql.cli --query "list products" --llm-config configs/llm.yaml
  ```
- After install:
  ```bash
  nl2sql-cli --query "list products" --llm-config configs/llm.yaml
  ```
Flags:
- `--config`: datasource YAML (default `configs/datasources.yaml` or `DATASOURCE_CONFIG`)
- `--id`: datasource id (default `manufacturing_sqlite`)
- `--llm-config`: per-agent LLM mapping (default `configs/llm.yaml` or `LLM_CONFIG`)
- `--vector-store`: path to vector store (default `./chroma_db` or `VECTOR_STORE`)
- `--index`: run schema indexing (use with `--vector-store`)
- `--stub-llm`: run with a fixed stub plan (no live LLM)
- `--debug`: show output of each node in the graph (streaming)

## Examples

### 1. Indexing the Schema
Index the database schema into the vector store (default `./chroma_db`):
```bash
python -m src.nl2sql.cli --index
```
Specify a custom vector store path:
```bash
python -m src.nl2sql.cli --index --vector-store ./my_vector_store
```

### 2. Querying with Vector Store
Run a query using the indexed schema for context.
**Note:** You must run indexing (step 1) at least once before querying.
```bash
python -m src.nl2sql.cli --query "Show top 5 products" --vector-store ./chroma_db
```

### 3. Using Environment Variables
Set configuration via a `.env` file (or shell variables) to simplify commands.
Create a `.env` file:
```bash
OPENAI_API_KEY="sk-..."
VECTOR_STORE="./chroma_db"
```
Then run:
```bash
python -m src.nl2sql.cli --query "Show top 5 products"
```

### 4. Full Customization
- `DATASOURCE_CONFIG`: Path to datasource config YAML

## Benchmarking
Compare performance of different LLM configurations (latency, success rate, token usage).

1. **Create a benchmark suite config** (e.g., `configs/benchmark_suite.yaml`):
   ```yaml
   gpt-4o-setup:
     default:
       provider: openai
       model: gpt-4o
   
   gpt-3.5-setup:
     default:
       provider: openai
       model: gpt-3.5-turbo
   ```

2. **Run the benchmark**:
   ```bash
   python -m src.nl2sql.cli --query "List production runs for Widget Alpha with machine and factory names" --benchmark --bench-config configs/benchmark_suite.yaml --iterations 3
   ```
   *Note: `--bench-config` defaults to `configs/benchmark_suite.yaml` if omitted.*

3. **View Results**:
   The CLI will output a comparison table:
   ```
   === Benchmark Results ===
   Config                    | Success  | Avg Latency  | Avg Tokens
   -----------------------------------------------------------------
   gpt-4o-setup              |  100.0% |       2.50s |      150.0
   gpt-3.5-setup             |   80.0% |       1.20s |      140.0
   ```

## Datasource Profiles
Configure in `configs/datasources.yaml`:
- `engine`, `sqlalchemy_url/DSN`, `statement_timeout_ms`, `row_limit`, `max_bytes`
- Feature flags: `allow_generate_writes`, `supports_dry_run`, etc.
- SQLite starter uses `row_limit: 100`; Postgres example provided (update URL/auth).

## LLM Configuration
`configs/llm.yaml` shows per-agent mapping. The registry loads:
- `default` provider/model
- `agents.intent`, `agents.planner`, `agents.generator` (override)
Keys are taken from config or `OPENAI_API_KEY`.

## Testing
- Run goldens against SQLite:
  ```bash
  python -m pytest tests/test_goldens_sqlite.py
  ```

## Project Structure
- `src/`: core modules (`nodes`, `langgraph_pipeline`, `datasource_config`, `llm_registry`, `cli`, etc.)
- `configs/`: datasource and LLM example configs
- `scripts/`: utilities (`setup_sqlite_manufacturing.py`)
- `docs/`: plan and goldens
- `tests/`: pytest goldens

## Agents (LangGraph)
- **Intent** (AI): normalizes the user query, extracts entities/filters/clarifications. Output: structured intent hints.
- **Schema** (non-AI): introspects the datasource (via SQLAlchemy) to list tables/columns for grounding and wildcard expansion.
- **Planner** (AI): produces a structured query plan (tables, joins, filters, aggregates, order_by, limit) via LLM with Pydantic validation.
- **SQL Generator** (AI): renders engine-aware SQL from the plan, enforces limits, rejects wildcards, and adds ORDER BY when present.
- **Validator** (non-AI): guards against DDL/DML, missing LIMIT, UNION/multi-statements, and missing ORDER BY when requested by the plan.
- **Executor** (non-AI): runs the SQL read-only against the datasource, returning row count and a sample for verification.

## Flow
```mermaid
flowchart TD
  user["User Query"] --> intent["Intention (AI)"]
  intent --> schema["Schema (non-AI)"]
  schema --> planner["Planner (AI)"]
  planner --> generator["SQL Generator (AI)"]
  generator --> validator["Validator (non-AI)"]
  validator --> executor["Executor (non-AI)"]
  executor --> answer["Answer/Result Sample"]
  subgraph Agents
    intent
    schema
    planner
    generator
    validator
    executor
  end
  style user fill:#f6f8fa,stroke:#aaa
  style answer fill:#f6f8fa,stroke:#aaa
```
## Notes
- Guardrails block DDL/DML, enforce LIMIT, reject UNION/multi-statements, and expand wildcards using schema metadata when possible.
- Execution is read-only; limits are clamped to datasource `row_limit`.
- To add another engine, create a profile and ensure the driver is installed; SQLAlchemy is used as the interface.
