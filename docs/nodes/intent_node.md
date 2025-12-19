# IntentNode

## Purpose

The `IntentNode` analyzes the user's natural language query to extract semantic meaning, keywords, and entities. It classifies the query type (e.g., READ, WRITE, DDL) and acts as a "query expander" to improve the accuracy of the subsequent schema retrieval step.

## Components

- **`LLM (Language Model)`**: A configured LLM (e.g., GPT-4, Claude) used to reason about the query.
- **`IntentModel`**: Pydantic model defining the structured output format.
- **`INTENT_PROMPT`**: The system prompt used to guide the LLM's analysis.

## Inputs

The node reads the following fields from `GraphState`:

- `state.user_query`: The original natural language query from the user.

## Outputs

The node updates the following fields in `GraphState`:

- `state.intent`: A structured `IntentModel` object containing:
  - `query_type`: (e.g., "READ")
  - `keywords`: List of relevant domain terms extracted from the query.
  - `entities`: List of specific entities (e.g., "Customer X", "Order 123").
  - `query_expansion`: List of synonym terms to aid vector search.
  - `reasoning`: Textual explanation of why the intent was classified this way.
- `state.reasoning`: Log entry summarizing the extracted intent and classification.
- `state.errors`: Appends `PipelineError` (Code: `INTENT_EXTRACTION_FAILED`) if the LLM call fails.

## Logic Flow

1. **Prompt Construction**: Formats the `INTENT_PROMPT` with the `user_query`.
2. **LLM Invocation**: Calls the LLM to process the prompt and return a structured response (enforced via function calling or structured output).
3. **Parses Output**: Validates the LLM output against the `IntentModel` schema.
4. **State Update**: Populates `state.intent` with the parsed model.

## Error Handling

- **`INTENT_EXTRACTION_FAILED`**: Occurs if the LLM fails to respond or returns invalid JSON that cannot be parsed into `IntentModel`.
- **Missing LLM**: If initialized without an LLM, returns a dormant validation state (noop).

## Dependencies

- `langchain_core.prompts.ChatPromptTemplate`
- `nl2sql.nodes.intent.schemas.IntentModel`
