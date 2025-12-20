# NL2SQL System Architecture

This document provides a comprehensive technical overview of the NL2SQL system components, observability, registry, and guardrails.

## 1. High-Level Architecture

The system operates on an **Intent-Driven Map-Reduce** paradigm to handle complex user queries efficiently.

### Summary

1. **Intent Classification**: The **Intent Node** classifies the query as `TABLE` (raw data), `KPI` (single metric), or `SUMMARY` (analysis).
2. **Decomposition (Map)**: The **Decomposer Node** breaks complex queries into sub-queries per datasource.
3. **Parallel Execution**: Independent execution subgraphs run for each sub-query.
    * **Fast Lane**: For `TABLE`/`KPI` intents, simple SQL is executed directly.
    * **Agentic Loop**: For `SUMMARY` intents, a complex Planner/Validator loop is used.
4. **Aggregation (Reduce)**: The **Aggregator Node** synthesizes results. It skips LLM processing for Fast Lane queries.

---

## 2. Core Components: The Pipeline nodes

Each branch runs a dedicated LangGraph `StateMachine`.

### 2.1 Nodes

| Node | Responsibility | Key Inputs | Key Outputs |
| :--- | :--- | :--- | :--- |
| **IntentNode** | **Entry Point**. Classifies intent (`tabular`, `kpi`, `summary`), canonicalizes queries, and extracts entities. | `user_query` | `response_type`, `enriched_terms` |
| **DecomposerNode** | Breaks down queries into sub-queries. Uses `enriched_terms` for context retrieval. | `user_query`, `enriched_terms` | `sub_queries` |
| **DirectSQLNode** | (Fast Lane) Generates SQL for simple queries without a plan. | `user_query` | `sql_draft` |
| **SchemaNode** | Retrieves relevant table schemas. | `datasource_id` | `schema_info` |
| **PlannerNode** | (Agentic Lane) Generates an abstract execution plan. | `schema_info` | `plan` |
| **ValidatorNode** | (Agentic Lane) Guardrails. Checks column existence and types. | `plan`, `schema_info` | `errors` |
| **GeneratorNode** | Converts abstract plan into dialect-specific SQL. | `plan` | `sql_draft` |
| **ExecutorNode** | Executes SQL. | `sql_draft` | `execution` |
| **AggregatorNode** | Synthesizes results. Returns raw data for Fast Lane or LLM summary for Slow Lane. | `intermediate_results`, `response_type` | `final_answer` |

### 2.2 State Management (`GraphState`)

State is managed via a Pydantic model (`src/nl2sql/schemas.py`), ensuring strict typing across nodes.

```python
class GraphState(BaseModel):
    user_query: str
    response_type: Literal["tabular", "kpi", "summary"]
    enriched_terms: List[str]
    sub_queries: Optional[List[SubQuery]]
    selected_datasource_id: Optional[str]
    execution: Optional[ExecutionModel]
    intermediate_results: List[Any]
    # ...
```

---

## 3. Observability & Callbacks

The system emphasizes "glass-box" execution, providing deep visibility into the AI's decision-making process through a robust Callback system.

### 3.1 `ObservabilityCallback` (`src/nl2sql/callbacks/observability.py`)

* **Purpose**: The primary tracing engine.
* **Function**: Hooks into LangChain's `on_chain_start`, `on_chain_end`, and `on_llm_end`.
* **Capture**:
  * Captures inputs/outputs of every node.
  * Extracts "reasoning" (thoughts) from AI nodes.
  * accumulates token usage.
  * Updates the global `LATENCY_LOG` and `TOKEN_LOG`.

### 3.2 `StatusCallback` (`src/nl2sql/callbacks/status.py`)

* **Purpose**: Real-time CLI feedback (UX).
* **Function**: Controls the usage of `rich` spinners and status messages.
* **Behavior**:
  * `on_chain_start`: "Thinking... [Node Name]"
  * `on_chain_end`: "✔ [Node Name] Completed (0.45s)"
  * `on_chain_error`: "❌ [Node Name] Failed"

### 3.3 `TokenUsageCallback`

* **Purpose**: Cost tracking.
* **Function**: Standardizes token counting across different LLM providers (OpenAI, Anthropic, Gemini).

---

## 4. Registry Pattern

To support dynamic configuration and dependency injection, we use registries:

### 4.1 `DatasourceRegistry`

* Manages database connection profiles (`DatasourceProfile`).
* **New Feature**: Supports `date_format` configuration (e.g., "DD-MM-YYYY") per profile, which is dynamically injected into the Planner and Validator.
* Handles `SQLAlchemy` engine creation and connection pooling.

### 4.2 `LLMRegistry`

* Manages LLM instances (Planner LLM, Router LLM, etc.).
* Allows swapping models (e.g., GPT-4 for Planning, GPT-3.5 for Summarization) via configuration.

---

## 5. Reporting & CLI

The CLI (`src/nl2sql/reporting.py`) is decoupled from the logic.

* **`ConsolePresenter`**: A facade for the `rich` library.
  * `print_execution_tree`: Renders the hierarchical Map-Reduce execution logic.
  * `print_performance_report`: Renders detailed tables for latency and token costs.
  * `print_sql`: Syntax-highlighted SQL blocks.

## 6. Validation & Guardrails Strategy

We employ a **Defense-in-Depth** strategy:

1. **Planner Constraints**: Prompt engineering ensures the LLM is aware of schema types.
2. **Validator Logic**:
    * **Structure**: Group By/Aggregation validity.
    * **Schema**: Column/Table existence checks.
    * **Data Types**: Checks if a string literal is used for an `INTEGER` column.
    * **Formats**: Checks if a date literal matches the configured `date_format` (e.g. `2023-12-31` vs `31/12/2023`).
3. **Generator Safety**: Only specific SQL dialects allowed; read-only enforcement capabilities.
