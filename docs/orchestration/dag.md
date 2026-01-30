# DAG / Graph Orchestration Architecture

Orchestration is expressed as a LangGraph `StateGraph` built by `build_graph()` and a logical `ExecutionDAG` built by `GlobalPlannerNode`. Routing across subgraphs is handled by `build_scan_layer_router()`.

## Orchestration layers

- **Control graph**: the LangGraph pipeline in `nl2sql.pipeline.graph`.
- **Logical DAG**: a deterministic `ExecutionDAG` representing scans, combines, and post-combine operations.

## ExecutionDAG structure

`ExecutionDAG` consists of `LogicalNode` and `LogicalEdge` instances. `ExecutionDAG._layered_toposort()` computes `layers` for deterministic scheduling.

```mermaid
graph TD
    Scan1[scan: subquery_1] --> Combine[combine: group_1]
    Scan2[scan: subquery_2] --> Combine
    Combine --> PostAgg[post_aggregate]
    PostAgg --> PostSort[post_sort]
```

## Routing logic

`build_scan_layer_router()` selects the next scan layer, resolves the compatible subgraph based on datasource capabilities, and dispatches parallel subgraph executions via LangGraph `Send` objects.

```mermaid
sequenceDiagram
    participant Planner as GlobalPlannerNode
    participant Router as build_scan_layer_router
    participant Subgraph as SQL Agent Subgraph
    participant Aggregator as EngineAggregatorNode

    Planner->>Router: ExecutionDAG + sub_queries
    Router->>Subgraph: Send(payload per scan node)
    Subgraph-->>Router: subgraph outputs + artifacts
    Router->>Aggregator: When all scan nodes completed
```

## Source references

- Graph builder: `packages/core/src/nl2sql/pipeline/graph.py`
- Routing logic: `packages/core/src/nl2sql/pipeline/routes.py`
- Execution DAG models: `packages/core/src/nl2sql/pipeline/nodes/global_planner/schemas.py`
- Global planner: `packages/core/src/nl2sql/pipeline/nodes/global_planner/node.py`
