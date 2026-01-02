# GraphState API

The `GraphState` object represents the shared memory passed between nodes in the LangGraph pipeline.

## Attributes

| Field | Type | Description |
| :--- | :--- | :--- |
| `user_query` | `str` | Canonical user query. |
| `sql_draft` | `str` | The Generated SQL string (from Generator). |
| `plan` | `PlanModel` | The AST produced by the Planner. |
| `relevant_tables` | `List[Table]` | subset of schema found by Vector Search. |
| `user_context` | `Dict` | User identity (`role`, `allowed_tables`) for Policy Logic. |
| `errors` | `List[PipelineError]` | Accumulating list of errors (Validation/Execution). |
| `reasoning` | `List[Dict]` | Log of "thoughts" from agents. |
| `execution` | `ExecutionModel` | Final database results. |
| `sub_queries` | `List[SubQuery]` | If Map-Reduce was triggered. |
