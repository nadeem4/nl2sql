# Conversations API

!!! note "C1 is built; `tool`, `report` and `engine.chat_tools.list` arrive with C2"
    The three C2 methods have their final signatures and raise `NotImplementedError` naming their row.

## Purpose
`engine.conversations`: model-led conversations over the database ([Conversations](../../conversations/index.md)),
and `engine.chat_tools` ([Chat tools](../../conversations/tools.md)).

## Key Modules
- `packages/nl2sql/src/nl2sql/api/conversations_api.py` (`ConversationsAPI`, internal)
- `packages/nl2sql/src/nl2sql/conversations/` (`api.py` the turn and its loop, `agent.py` the `[CONVERSATION]`
  block and the step checks, `prompts.py`, `store.py` ownership and stored rows)
- `packages/nl2sql/src/nl2sql/api/chat_tools_api.py` (`ChatToolsAPI`, internal)
- `packages/nl2sql/src/nl2sql/conversations/models.py` (`Conversation`, `Turn`, `ToolStep`, `ModelStep`)

## Public Surface

Every method takes `user_context` keyword-only and required. A conversation that is not the caller's is
`ConversationNotFound`; one already running a turn is `ConversationBusy`. `ValueError` is raised, before
anything runs, for: neither or both of `text` and `choice`, a `choice` with nothing pending or not one of the
pending options, an empty message, a conversation at `max_turns` (`CONVERSATION_TOO_LONG`), `retry` with no
earlier question.

| Method | Row |
|---|---|
| `start(*, user_context, datasource_id=None, dashboard_id=None) -> Conversation` | C1 |
| `ask(conversation_id, text=None, *, user_context, choice=None, execute=True, cancellation_token=None) -> Turn` | C1 |
| `stream(conversation_id, text=None, *, user_context, choice=None, execute=True) -> Iterator[Event]` | C1 |
| `retry(conversation_id, *, user_context, approvals=()) -> Turn` | C1 |
| `ask_only(conversation_id, question, *, user_context, choice=None) -> QueryResult` | C1 |
| `get(conversation_id, *, user_context) -> Conversation` | C1 |
| `list(*, user_context, limit=20) -> list[ConversationInfo]` | C1 |
| `delete(conversation_id, *, user_context) -> None` (`ConversationBusy` while a turn runs) | C1 |
| `tool(conversation_id, name, args=None, *, user_context) -> Turn` | C2 |
| `report(conversation_id, *, user_context, turns=None, title=None) -> str` | C2 |
| `engine.chat_tools.list() -> list[ChatToolInfo]` | C2 |

- `start(dashboard_id=...)` needs a dashboard the caller owns (`DashboardNotFound` otherwise).
- `ask` returns the stored `Turn`: `status` is that of the last step that did not end `ok`, else `answered`;
  `error` holds `{"code", "message"}` when the loop itself ended it (`MODEL_STEP_INVALID`, or
  `RUN_LIMIT_REACHED` for `stopped`, or the `PROVIDER_*` code when the model provider refused a step). A `cancellation_token` that is cancelled ends the turn `error` with
  `CANCELLED` after the step in flight.
- `stream` ends with `conversation.turn`; see [Streaming](../../conversations/index.md#streaming).
- `ask_only` returns the run's `QueryResult` with its rows; the stored turn has none.