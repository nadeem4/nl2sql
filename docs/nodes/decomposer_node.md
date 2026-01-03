# DecomposerNode

## Purpose

The `DecomposerNode` acts as the entry point and router for the pipeline. It is responsible for analyzing the user's query to determine which datasource(s) should handle the request. For complex requests, it can break the query down into sub-queries (though simple routing is the primary function). It also checks user authorization before proceeding.

## Class Reference

- **Class**: `DecomposerNode`
- **Path**: `packages/core/src/nl2sql/pipeline/nodes/decomposer/node.py`

## Inputs

The node reads the following fields from `GraphState`:

- `state.user_query` (str): The initial user question.
- `state.user_context` (Dict): User session data, specifically `allowed_datasources` for authorization.
- `state.semantic_analysis` (SemanticAnalysisResponse): Used to expand the query with keywords/synonyms for better vector retrieval.

## Outputs

The node updates the following fields in `GraphState`:

- `state.sub_queries` (List[SubQuery]): A list of routed queries. Each `SubQuery` contains:
  - `question`: The specific question for the datasource.
  - `datasource_id`: The ID of the chosen datasource.
- `state.confidence` (float): The confidence score of the routing decision.
- `state.reasoning` (List[Dict]): Explanation of why a specific datasource was selected.
- `state.errors` (List[PipelineError]): `SECURITY_VIOLATION` if the user lacks access.

## Logic Flow

1. **Authorization Check**: Verifies if `state.user_context` contains accessible datasources. If not, returns `SECURITY_VIOLATION`.
2. **Query Expansion**: If `state.semantic_analysis` is present, it augments the query with keywords and synonyms to improve retrieval recall.
3. **Context Retrieval**:
    - Queries the `OrchestratorVectorStore` using the expanded query.
    - Retrieves relevant table schemas and datasource descriptions.
4. **LLM Routing**:
    - Uses the LLM to analyze the retrieved context and the user query.
    - Decides which datasource is best suited to answer the question.
5. **Output Generation**: Returns the routing decision (datasource selection) and confidence score.

## Error Handling

- **`SECURITY_VIOLATION`**: Critical error if the user has no allowed datasources.
- **Retrieval Warnings**: Logs warnings if no relevant documents are found in the vector store.
