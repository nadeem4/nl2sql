# High-Level System Architecture

NL2SQL is organized around an **orchestration graph** and a **context object** that wires registries and storage. The entry point `run_with_graph()` builds the graph (`build_graph()`), injects a `GraphState`, and invokes the compiled LangGraph pipeline.

## Key runtime components

- `NL2SQLContext` (`nl2sql.context.NL2SQLContext`) initializes registries and stores.
- `build_graph()` (`nl2sql.pipeline.graph.build_graph`) builds the LangGraph pipeline.
- `run_with_graph()` (`nl2sql.pipeline.runtime.run_with_graph`) executes the pipeline with timeout and cancellation.
- `GraphState` (`nl2sql.pipeline.state.GraphState`) is the shared state for the pipeline.

## Component topology

```mermaid
flowchart TD
    UserQuery[User Query] --> Runtime[run_with_graph()]
    Runtime --> Graph[build_graph()]
    Graph --> Resolver[DatasourceResolverNode]
    Resolver --> Decomposer[DecomposerNode]
    Decomposer --> Planner[GlobalPlannerNode]
    Planner --> Router[build_scan_layer_router]
    Router --> Subgraph[SQL Agent Subgraph]
    Subgraph --> Router
    Router --> Aggregator[EngineAggregatorNode]
    Aggregator --> Synthesizer[AnswerSynthesizerNode]

    subgraph Context[NL2SQLContext]
        DS[DatasourceRegistry]
        LLM[LLMRegistry]
        VS[VectorStore]
        SS[SchemaStore]
        AS[ArtifactStore]
        RS[ResultStore]
    end
```

## Runtime flow (summary)

1. `run_with_graph()` constructs `GraphState` and invokes the compiled graph.
2. `DatasourceResolverNode` selects compatible datasources.
3. `DecomposerNode` splits complex questions into sub-queries.
4. `GlobalPlannerNode` produces an `ExecutionDAG` representing scan/combine/post operations.
5. `build_scan_layer_router()` dispatches sub-queries to the SQL agent subgraph.
6. `EngineAggregatorNode` loads artifacts and applies `ExecutionDAG` using the aggregation engine.
7. `AnswerSynthesizerNode` formats the final response.

## Source references

- Graph construction: `packages/core/src/nl2sql/pipeline/graph.py`
- Runtime execution: `packages/core/src/nl2sql/pipeline/runtime.py`
- Shared state: `packages/core/src/nl2sql/pipeline/state.py`
- Context initialization: `packages/core/src/nl2sql/context.py`
