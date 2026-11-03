# Conversations

How a chat turn runs and what is stored afterwards, for contributors changing the chat loop and operators who want to know what a conversation keeps.

How to use conversations is in [Conversations](../conversations/index.md); the methods are in [Conversations API](../api/core/conversations.md).

## The model leads, the pipeline answers

A conversation is a loop around the same pipeline that answers a single question. On each step the model makes one structured call (`ModelStep`) and either replies or calls a tool:

- the `ask` tool, which carries a question that stands alone and runs it as a normal run: typed plan, validator, cost rule, the caller's scope, hooks and events;
- an enabled [chat tool](../conversations/tools.md) (a chart, a diagram, a report, a dashboard edit), which runs from a registry with no model call of its own.

The model never writes SQL and never sees result rows. It sees the last `context_turns` turns as one JSON object: the questions that ran, each answer's summary, the columns, and the datasources and tables read. Earlier text, including the new message, reaches it as a JSON string value, so text in the history cannot pass for an instruction. The code is in `packages/nl2sql/src/nl2sql/conversations/` (`agent.py` is the loop, `tools.py` the registry).

## Bounds

At most `max_tool_calls` tool calls run per turn, then one more model call with tools off, so a turn makes at most `max_tool_calls + 1` model calls. Those count against the run caps (`settings.pipeline.max_llm_calls`, `max_cost_usd`). A step the model gets wrong (an unknown tool, a bad argument) is sent back with what was wrong; after the parse retries the turn ends with `MODEL_STEP_INVALID`.

Each `ask` step is a run of its own with its own `trace_id` and its own timeout on a child cancellation token. A timeout fails the step, not the turn, so the model can reply with a narrower suggestion; cancelling the turn still stops the step in flight.

## Clarifications and approvals

A run that ends `needs_clarification` ends the turn and records a pending question on the conversation. The next turn answers it with an option id, which skips the conversation model, or with text the model maps to an option. A run that ends `needs_approval` ends the turn; an approver grants the request elsewhere, and the asker's `retry` re-runs the last `ask` step's question without calling the conversation model. See [Access and security](access-and-security.md) for how approvals are stored.

## What is stored

Conversations live in the state store (the `conversations` table in `<storage.path>/state.db`). A stored turn keeps:

- the user's text and the model's reply;
- for each step, the question, the plan, the SQL, the explanation and the summary, and the run's `trace_id`;
- usage.

It never keeps result rows. Rows are read with `engine.results.page(trace_id, ..., user_context=caller)` for as long as the result files exist, and the same owner check applies.

Roles are never stored. Each turn runs with the `UserContext` passed to that call, so a role taken away applies at the next turn.

## One turn at a time

A turn holds the state store's lock `conversation:<id>`. A second `ask`, `retry` or `stream` on the same conversation raises `ConversationBusy`, which the API reports as 409. Closing a stream early cancels the turn and stores nothing.

## Owner and retention

A conversation belongs to the `(tenant_id, user_id)` that started it. Any other caller gets `ConversationNotFound`, the same error as for an id that does not exist, so ids reveal nothing. `conversations.max_turns` caps a conversation's length, and `engine.storage.purge()` deletes conversations not updated within `conversations.max_age_days`.

## Why

Putting the model in charge of the dialogue and the validator in charge of the data keeps each to what it is good at: the model decides what to ask next, and every question it asks still passes the same checks as one typed by hand. Storing plans and summaries instead of rows means a conversation never becomes a second copy of the data. See [Plans and SQL](plans-and-sql.md).
