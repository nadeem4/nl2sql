# NL2SQL Map-Reduce Architecture

This document outlines the **Map-Reduce** pattern implemented in the NL2SQL pipeline to handle complex queries that span multiple datasources or require decomposing a complex problem into sub-problems.

## 1. Overview

The pipeline uses a dynamic branching strategy powered by `LangGraph`'s `Send` API to execute independent sub-queries in parallel (Map Phase) and collect their results via state reducers for final synthesis (Reduce Phase).

```mermaid
graph TD
    UserQuery[User Query] --> Intent[Intent Node]
    Intent --> Decomposer[Decomposer Node]
    Decomposer -- "Splits Query" --> MapBranching[Fan Out (Map)]

    subgraph "Execution Branch (Parallel)"
        MapBranching --> Schema[Schema Node]
        Schema --> RouteLogic{Route Logic}
        RouteLogic -- "Fast Lane" --> DirectSQL[DirectSQL Node]
        DirectSQL --> FastExecutor[Executor]
        
        RouteLogic -- "Slow Lane" --> Planner[Planner Node]
        Planner --> Validator
        Validator --> Generator
        Generator --> Executor
    end

    FastExecutor -- "Appends Result" --> StateAggregation[State Reducers]
    Executor -- "Appends Result" --> StateAggregation
    StateAggregation --> Aggregator[Aggregator (Reduce)]
    Aggregator --> FinalAnswer
```

## 2. Map Phase: Decomposition & Fan-Out

**Node**: `DecomposerNode` (`src/nl2sql/nodes/decomposer`)

1. **Input**: The canonicalized `user_query` and `enriched_terms` from the **IntentNode**.
2. **Process**: An LLM analyzes the query to determine if it needs to be split, using vector context.
    * *Simple Query*: Returns original query (Single branch).
    * *Complex Query*: Returns list of independent `sub_queries` (e.g., "Sales in 2023" and "Sales in 2022").
3. **Fan-Out Mechanism**:
    * The `continue_to_subqueries` conditional edge in `langgraph_pipeline.py` iterates over `state.sub_queries`.
    * It generates a `Send("execution_branch", payload)` event for each sub-query.
    * **Payload**: `{"user_query": sub_query, "datasource_id": ..., "selected_datasource_id": ...}`.

## 3. Parallel Processing: Independent Branches

Each sub-query triggers an isolated run of the `execution_subgraph`. These run in parallel.

* **Routing**: The branch routes to either **DirectSQL** (Fast Lane) or **Agentic Loop** (Slow Lane) based on `response_type`.
* **SchemaNode**: Fetches relevant schema for that datasource.
* **Execution**: Generates and executes SQL.

## 4. State Reduction: Collecting Results

As parallel branches complete, they return update dictionaries. Since multiple branches write to the same state keys, we use **Annotated Reducers** in `GraphState` (`src/nl2sql/schemas.py`) to merge them safely.

| State Key | Reducer | Purpose |
| :--- | :--- | :--- |
| `query_history` | `operator.add` | Appends all execution results (SQL, rows, errors) from all branches into a single list. |
| `intermediate_results` | `operator.add` | Collects intermediate analysis artifacts. |
| `thoughts` | `reduce_thoughts` | Merges log/thought streams from multiple agents (keyed by agent name). |
| `datasource_id` | `merge_ids_set` | Unions all accessed datasource IDs. |
| `routing_info` | `merge_dicts` | Merges routing metadata for all accessed datasources. |

**Crucial Note**: Fields like `sql_draft` or `execution` are *not* reduced (simple replacement). Therefore, parallel branches **must not** return these keys to the global state to avoid overwrite conflicts. They return data via `query_history`.

## 5. Reduce Phase: Aggregation

**Node**: `AggregatorNode` (`src/nl2sql/nodes/aggregator`)

1. **Input**: The fully reduced `state.query_history` containing results from all branches.
2. **Process**:
    * The Aggregator LLM reads the original `user_query` and the list of `query_history` items.
    * It synthesizes a natural language answer that combines insights from all sub-queries.
    * Example: "Sales in 2023 were X (from Branch A) and 2022 were Y (from Branch B), showing a Z% increase."
3. **Output**: Populates `state.final_answer`.

## 6. Benefits

* **Scalability**: Can query 10 different databases simultaneously.
* **Isolation**: An error in one sub-query branch (e.g., table not found) is captured in `query_history` but doesn't necessarily crash the entire pipeline or block other branches.
* **Clarity**: The Aggregator sees exactly which sub-query produced which result.
