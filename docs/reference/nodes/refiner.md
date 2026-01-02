# Refiner Node

**Role**: The Critic / Teacher.

Previously known as "Summarizer". It sits in the feedback loop.

## Logic

1. Receives `state.errors` (from Logical or Physical validators).
2. Receives the failed `state.plan` and `state.sql_draft`.
3. Uses an LLM to analyze the discrepancy.
4. Generates a natural language **Feedback Message** for the Planner.
    - *Example*: "The column 'usr_id' does not exist in table 'users'. Did you mean 'user_id'?"

## Outputs

- Updates `state.retry_count`.
- Passes feedback back to `Planner` for the next iteration.
