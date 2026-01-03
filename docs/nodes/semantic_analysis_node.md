# SemanticAnalysisNode

## Purpose

The `SemanticAnalysisNode` is a preprocessing step that normalizes the user query and extracts metadata such as keywords and synonyms. This enriched context supports both the `DecomposerNode` (by expanding the search query for better retrieval) and the `PlannerNode` (by resolving ambiguity).

## Class Reference

- **Class**: `SemanticAnalysisNode`
- **Path**: `packages/core/src/nl2sql/pipeline/nodes/semantic/node.py`

## Inputs

The node reads the following fields from `GraphState`:

- `state.user_query` (str): The raw user input.

## Outputs

The node updates the following fields in `GraphState`:

- `state.semantic_analysis` (`SemanticAnalysisResponse`):
  - `canonical_query`: Normalized form of the question.
  - `keywords`: Extracted domain keywords.
  - `synonyms`: List of potential synonyms for columns/tables.
  - `reasoning`: The analysis thought process.

## Logic Flow

1. **LLM Invocation**:
    - Prompts the LLM with the `user_query`.
    - Requests an analysis including canonicalization and keyword extraction.
2. **Result Storage**:
    - Stores the structured response in `state.semantic_analysis`.
    - Logs the reasoning and keywords.

## Error Handling

- **Fallback**: If the LLM call fails, it defaults to returning the raw query with empty keywords to prevent pipeline blockage.
