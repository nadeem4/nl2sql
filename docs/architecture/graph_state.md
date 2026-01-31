# GraphState Architecture

## Overview
GraphState is the shared, typed state container for the NL2SQL pipeline's LangGraph execution. It exists to provide a single, structured place where node outputs, errors, reasoning, and subgraph artifacts accumulate as the graph runs. Its scope is the main pipeline graph built in `packages/core/src/nl2sql/pipeline/graph.py`, and it is the input/output contract for graph nodes declared in that graph.

---

## GraphState Definition

### Primary class
- `GraphState` in `packages/core/src/nl2sql/pipeline/state.py`

Fields (exact names and types from code):
- `trace_id: str`
- `user_query: str`
- `user_context: UserContext`
- `datasource_id: Optional[str]`
- `datasource_resolver_response: Optional[DatasourceResolverResponse]`
- `decomposer_response: Optional[DecomposerResponse]`
- `global_planner_response: Optional[GlobalPlannerResponse]`
- `aggregator_response: Optional[AggregatorResponse]`
- `answer_synthesizer_response: Optional[AnswerSynthesizerResponse]`
- `artifact_refs: Annotated[Dict[str, ArtifactRef], update_results]`
- `subgraph_outputs: Annotated[Dict[str, SubgraphOutput], update_results]`
- `errors: Annotated[List[PipelineError], operator.add]`
- `reasoning: Annotated[List[Dict[str, Any]], operator.add]`
- `warnings: Annotated[List[Dict[str, Any]], operator.add]`
- `subgraph_id: Optional[str]`
- `subgraph_name: Optional[str]`

Reducer behavior in `GraphState`:
- `update_results(current, new)` merges dicts as `{**current, **new}`.
- `operator.add` concatenates lists for `errors`, `reasoning`, and `warnings`.

### Subgraph state boundary
- `SubgraphExecutionState` in `packages/core/src/nl2sql/pipeline/state.py` is the per-subgraph state used by the SQL agent subgraph.

Fields (exact names and types from code):
- `trace_id: str`
- `sub_query: Optional[SubQuery]`
- `user_context: Optional[UserContext]`
- `subgraph_id: Optional[str]`
- `subgraph_name: Optional[str]`
- `relevant_tables: List[Table]`
- `ast_planner_response: Optional[ASTPlannerResponse]`
- `logical_validator_response: Optional[LogicalValidatorResponse]`
- `physical_validator_response: Optional[PhysicalValidatorResponse]`
- `generator_response: Optional[GeneratorResponse]`
- `executor_response: Optional[ExecutorResponse]`
- `refiner_response: Optional[RefinerResponse]`
- `retry_count: int`
- `errors: Annotated[List[PipelineError], operator.add]`
- `reasoning: Annotated[List[Dict[str, Any]], operator.add]`
- `warnings: Annotated[List[Dict[str, Any]], operator.add]`

### Subgraph output structure
- `SubgraphOutput` in `packages/core/src/nl2sql/pipeline/subgraphs/schemas.py`

Fields:
- `sub_query: Optional[SubQuery]`
- `subgraph_id: str`
- `subgraph_name: Optional[str]`
- `retry_count: int`
- `plan: Optional[PlanModel]`
- `sql_draft: Optional[str]`
- `artifact: Optional[ArtifactRef]`
- `errors: List[PipelineError]`
- `reasoning: List[Dict[str, Any]]`
- `status: Optional[str]`

---

## Field Lifecycle

The lifecycle below lists creation, mutation, reads, and resets based strictly on code.

### `trace_id`
- Creation: default factory in `GraphState` (`uuid4`) if not provided; always set in `run_with_graph` when `GraphState` is instantiated.
- Mutation: not mutated after creation in this repository.
- Read points: `build_scan_payload` and `wrap_subgraph` use it; `ExecutorRequest` uses it in `ExecutorNode`.
- Reset: none.

### `user_query`
- Creation: passed into `GraphState` in `run_with_graph`.
- Mutation: none in code.
- Read points: `DatasourceResolverNode`, `DecomposerNode`, `AnswerSynthesizerNode`.
- Reset: none.

### `user_context`
- Creation: passed into `GraphState` in `run_with_graph` (or default factory).
- Mutation: none in code.
- Read points: `DatasourceResolverNode` (RBAC), `ExecutorNode`, and subgraph state in `wrap_subgraph`.
- Reset: none.

### `datasource_id`
- Creation: passed into `GraphState` in `run_with_graph`.
- Mutation: none in code.
- Read points: `DatasourceResolverNode` (explicit override).
- Reset: none.

### `datasource_resolver_response`
- Creation/mutation: returned by `DatasourceResolverNode` as `datasource_resolver_response`.
- Read points: `resolver_route`, `DecomposerNode`, and `build_scan_payload`.
- Reset: none in code.

### `decomposer_response`
- Creation/mutation: returned by `DecomposerNode` as `decomposer_response`.
- Read points: `GlobalPlannerNode`, `build_scan_payload`, and `wrap_subgraph` (to find `sub_query` by id).
- Reset: none in code.

### `global_planner_response`
- Creation/mutation: returned by `GlobalPlannerNode` as `global_planner_response`.
- Read points: `build_scan_layer_router` and `EngineAggregatorNode`.
- Reset: none in code.

### `aggregator_response`
- Creation/mutation: returned by `EngineAggregatorNode` as `aggregator_response`.
- Read points: `AnswerSynthesizerNode`.
- Reset: none in code.

### `answer_synthesizer_response`
- Creation/mutation: returned by `AnswerSynthesizerNode` as `answer_synthesizer_response`.
- Read points: none in core pipeline; read in CLI reporting.
- Reset: none in code.

### `artifact_refs`
- Creation/mutation: returned by `wrap_subgraph` as `artifact_refs` (keyed by sub-query id). Merged by `update_results`.
- Read points: `build_scan_layer_router` (to skip completed scan nodes), `EngineAggregatorNode` (inputs to aggregation).
- Reset: none in code.

### `subgraph_outputs`
- Creation/mutation: returned by `wrap_subgraph` as `subgraph_outputs` (keyed by `subgraph_id`). Merged by `update_results`.
- Read points: CLI `run_pipeline` and `BenchmarkRunner` consume for reporting.
- Reset: none in code.

### `errors`
- Creation/mutation: appended in multiple nodes; reducer is list concatenation.
- Read points: routing logic and subgraph handling (e.g., `refiner` uses `state.errors`), CLI reporting.
- Reset: none in code.

### `reasoning`
- Creation/mutation: appended in multiple nodes; reducer is list concatenation.
- Read points: `RefinerNode` and CLI reporting.
- Reset: none in code.

### `warnings`
- Creation/mutation: appended in nodes that emit warnings in the main graph (e.g., `DatasourceResolverNode`).
- Read points: CLI reporting.
- Reset: none in code.

### `subgraph_id`
- Creation/mutation: created in `build_scan_payload` and passed to subgraph nodes.
- Read points: `wrap_subgraph` uses it to determine `sub_query` id and build `SubgraphOutput`.
- Reset: none in code.

### `subgraph_name`
- Creation/mutation: created in `build_scan_payload` and passed to subgraph nodes.
- Read points: `wrap_subgraph` and `ExecutorRequest` via `ExecutorNode`.
- Reset: none in code.

---

## Ownership Model

Ownership is defined by which node returns updates for a field:
- `datasource_resolver_response`: `DatasourceResolverNode`
- `decomposer_response`: `DecomposerNode`
- `global_planner_response`: `GlobalPlannerNode`
- `aggregator_response`: `EngineAggregatorNode`
- `answer_synthesizer_response`: `AnswerSynthesizerNode`
- `artifact_refs`: `wrap_subgraph` (subgraph wrapper in `graph_utils.py`)
- `subgraph_outputs`: `wrap_subgraph`
- `errors`, `reasoning`: emitted by many nodes and merged by list reducers
- `warnings`: emitted by nodes that return `warnings` in the main graph (currently `DatasourceResolverNode`)
- `subgraph_id`, `subgraph_name`: created in `build_scan_payload`, used only for subgraph execution context
- `trace_id`, `user_query`, `user_context`, `datasource_id`: owned by the pipeline entrypoint (`run_with_graph`)

Shared vs private:
- Shared: `errors`, `reasoning`, `warnings`, `artifact_refs`, `subgraph_outputs` are aggregate fields merged across branches.
- Private/branch-scoped: `subgraph_id`, `subgraph_name` are injected into subgraph payloads for a single subgraph execution.

---

## State Flow Across DAG

Step-by-step execution flow as defined in `build_graph` and routing:
1. `run_with_graph` constructs `GraphState` with `user_query`, `user_context`, and optional `datasource_id`, then calls `graph.invoke(initial_state.model_dump())`.
2. `DatasourceResolverNode` runs first and populates `datasource_resolver_response`, `reasoning`, and `errors`.
3. `resolver_route` decides whether to continue based on `datasource_resolver_response`.
4. `DecomposerNode` produces `decomposer_response` and reasoning.
5. `GlobalPlannerNode` produces `global_planner_response` (including the `ExecutionDAG`).
6. `build_scan_layer_router` emits `Send` branches using `build_scan_payload` for each pending scan node. The payload contains `subgraph_id`, `subgraph_name`, `trace_id`, `user_context`, `decomposer_response`, and `datasource_resolver_response`.
7. Each subgraph is wrapped by `wrap_subgraph`, which:
   - Builds a `SubgraphExecutionState` using the payload and a `sub_query` resolved from `decomposer_response`.
   - Invokes the subgraph and validates the result into `SubgraphExecutionState`.
   - Returns updates for `artifact_refs`, `subgraph_outputs`, `errors`, and `reasoning`.
8. The router checks `artifact_refs` to decide which scan nodes are still pending. When none remain, it routes to `aggregator`.
9. `EngineAggregatorNode` consumes `global_planner_response` and `artifact_refs` to produce `aggregator_response`.
10. `AnswerSynthesizerNode` consumes `aggregator_response` and `decomposer_response` to produce `answer_synthesizer_response`.

Subgraph internal flow uses `SubgraphExecutionState` and is defined in `build_sql_agent_graph`:
`schema_retriever` -> `ast_planner` -> `logical_validator` -> `generator` -> `executor`, with a retry loop via `retry_handler` and `refiner`.

---

## Mutability Rules

From code:
- `GraphState` and `SubgraphExecutionState` are Pydantic `BaseModel` classes with `extra="ignore"` and `arbitrary_types_allowed=True`. No immutability or frozen settings are defined.
- List fields (`errors`, `reasoning`, `warnings`) are merged using `operator.add`.
- Dict fields (`artifact_refs`, `subgraph_outputs`) are merged using `update_results` ({**current, **new}).
- Subgraph boundary is a serialization boundary: `wrap_subgraph` uses `SubgraphExecutionState(...).model_dump()` to pass data into the subgraph and `SubgraphExecutionState.model_validate(...)` to rehydrate the result.

No explicit copy-on-write mechanisms are defined beyond Pydantic serialization at subgraph boundaries.

---

## Merge Semantics

Defined reducers in `GraphState`:
- `update_results` merges dicts with `{**current, **new}`. Keys in `new` overwrite keys in `current`.
- `operator.add` concatenates list fields (`errors`, `reasoning`, `warnings`).

No explicit reducer is defined in this repository for scalar fields; any conflict resolution beyond these reducers is not specified in code here.

---

## Replayability

GraphState carries identifiers and artifact references that could support replay, but no replay or rehydration mechanism is implemented. See `failure_recovery.md` for replay support details and limitations.

---

## Determinism Impact
Determinism guarantees and non-determinism sources are documented in `determinism.md`. GraphState only carries the artifacts produced by those nodes (IDs, DAG hashes, errors, and diagnostics).

---

## Serialization + Persistence

Serialization:
- `GraphState` and `SubgraphExecutionState` are Pydantic models. `run_with_graph` calls `initial_state.model_dump()` before invoking the graph.
- `wrap_subgraph` uses `model_dump()` and `model_validate()` for subgraph boundaries.

Persistence:
- GraphState itself is not persisted in this repository.
- `artifact_refs` contain `ArtifactRef` entries with `uri`, `backend`, `format`, and `content_hash`, which reference artifacts stored elsewhere by executor components.

---

## Known Limitations

Based on code only:
- No explicit ordering guarantee is defined for merging parallel branch dict updates beyond `{**current, **new}`.
- No built-in persistence or snapshotting for GraphState is implemented.
- No explicit reset or cleanup semantics for accumulated fields (`errors`, `reasoning`, `warnings`, `artifact_refs`, `subgraph_outputs`).

---

## Related Code

GraphState and reducers:
- `packages/core/src/nl2sql/pipeline/state.py`

Graph construction and routing:
- `packages/core/src/nl2sql/pipeline/graph.py`
- `packages/core/src/nl2sql/pipeline/routes.py`
- `packages/core/src/nl2sql/pipeline/graph_utils.py`

Subgraph state and execution:
- `packages/core/src/nl2sql/pipeline/subgraphs/sql_agent.py`
- `packages/core/src/nl2sql/pipeline/subgraphs/schemas.py`

Mutators / consumers:
- `packages/core/src/nl2sql/pipeline/nodes/datasource_resolver/node.py`
- `packages/core/src/nl2sql/pipeline/nodes/decomposer/node.py`
- `packages/core/src/nl2sql/pipeline/nodes/global_planner/node.py`
- `packages/core/src/nl2sql/pipeline/nodes/aggregator/node.py`
- `packages/core/src/nl2sql/pipeline/nodes/answer_synthesizer/node.py`
- `packages/core/src/nl2sql/pipeline/nodes/schema_retriever/node.py`
- `packages/core/src/nl2sql/pipeline/nodes/ast_planner/node.py`
- `packages/core/src/nl2sql/pipeline/nodes/validator/node.py`
- `packages/core/src/nl2sql/pipeline/nodes/validator/physical_node.py`
- `packages/core/src/nl2sql/pipeline/nodes/generator/node.py`
- `packages/core/src/nl2sql/pipeline/nodes/executor/node.py`
- `packages/core/src/nl2sql/pipeline/nodes/refiner/node.py`

