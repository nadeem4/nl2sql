# SemanticAnalysisNode

## Purpose

The `SemanticAnalysisNode` is the **Entry Point** of the pipeline. It acts as the "Receptionist" that refines the raw user query before any routing or planning occurs. Its goal is to maximize retrieval accuracy by translating vague user requests into precise, domain-specific terminology.

## Key Logic

1. **Canonicalization**:
    * Transforms slang/ambiguity into standard English.
    * Example: "How many guys on the floor?" -> "Count count of operators on active shift".
2. **Enrichment**:
    * Generates list of synonymous keywords and entities.
    * Example: "Machine" -> ["Equipment", "CNC", "Asset", "Device"].

## Components

* **`LLM`**: Used to perform the text-to-JSON analysis.
* **`SemanticAnalysisResponse`**: The structured output schema.

## Inputs

* **`state.user_query`**: The raw natural language string from the user.

## Outputs

The node updates the following fields in `GraphState`:

* **`state.semantic_analysis`**: A dictionary containing:
  * `canonical_query`: The rewritten query.
  * `entities`: Extracted named entities.
  * `keywords`: Search terms for vector retrieval.
  * `intent`: High-level classification (Tabular vs Summary).
  * `reasoning`: Chain-of-thought explanation.

## Logic Flow

1. **Receive Query**: Accepts `state.user_query`.
2. **LLM Call**: Invokes the `semantic_analysis` agent (e.g., GPT-4o-mini).
3. **Parses Output**: Validates the JSON response against the Pydantic schema.
4. **State Update**: Populates `state.semantic_analysis`.

## Dependencies

* `nl2sql.nodes.semantic.schemas.SemanticAnalysisResponse`
