# Conversations API

!!! note "Arrives with C1 and C2"
    A stub: every method has its final signature and raises `NotImplementedError` naming its row.

## Purpose
`engine.conversations`: model-led conversations over the database ([Conversations](../../conversations/index.md)),
and `engine.chat_tools` ([Chat tools](../../conversations/tools.md)).

## Key Modules
- `packages/nl2sql/src/nl2sql/api/conversations_api.py` (`ConversationsAPI`, internal)
- `packages/nl2sql/src/nl2sql/api/chat_tools_api.py` (`ChatToolsAPI`, internal)
- `packages/nl2sql/src/nl2sql/conversations/models.py` (`Conversation`, `Turn`, `ToolStep`, `ModelStep`)

## Public Surface

Every method takes `user_context` keyword-only and required. A conversation that is not the caller's is
`ConversationNotFound`; one already running a turn is `ConversationBusy`.

| Method | Row |
|---|---|
| `start(*, user_context, datasource_id=None, dashboard_id=None) -> Conversation` | C1 |
| `ask(conversation_id, text=None, *, user_context, choice=None, execute=True, cancellation_token=None) -> Turn` | C1 |
| `stream(conversation_id, text=None, *, user_context, choice=None, execute=True) -> Iterator[Event]` | C1 |
| `retry(conversation_id, *, user_context, approvals=()) -> Turn` | C1 |
| `ask_only(conversation_id, question, *, user_context, choice=None) -> QueryResult` | C1 |
| `get(conversation_id, *, user_context) -> Conversation` | C1 |
| `list(*, user_context, limit=20) -> list[ConversationInfo]` | C1 |
| `delete(conversation_id, *, user_context) -> None` | C1 |
| `tool(conversation_id, name, args=None, *, user_context) -> Turn` | C2 |
| `report(conversation_id, *, user_context, turns=None, title=None) -> str` | C2 |
| `engine.chat_tools.list() -> list[ChatToolInfo]` | C2 |