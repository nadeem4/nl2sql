# Conversations

!!! note "Arrives with C1 (Wave C, 0.9)"
    This page is a stub. The shapes (`Conversation`, `Turn`, `ToolStep`, `ModelStep`) and the
    `engine.conversations` signatures exist; each method raises `NotImplementedError` naming the pull
    request that fills it. The design is [Wave C, section 3](../architecture/wave_c.md#3-conversation).

A conversation is a chat over the database. The model decides, turn by turn, whether to answer, to
ask the database a question through the same typed plan and validator as `run_query`, or to call a
[chat tool](tools.md). It never writes SQL and never sees result rows.

Every method of `engine.conversations` takes the caller (`user_context`, keyword-only and required): a
conversation is private to its owner, and an app that forgot the caller fails rather than seeing
everyone's. The methods are listed in [Conversations API](../api/core/conversations.md).

## What lands where

| Row | What it adds |
|---|---|
| C1 | `start`, `ask`, `stream`, `retry`, `ask_only`, `get`, `list`, `delete`: the model-led turn, clarifications and approvals in a conversation, ownership, retention |
| C2 | The chat-tool runner, `tool` and `report` ([Chat tools](tools.md)) |

## Configuration

`conversations:` in `nl2sql.yaml` (`context_turns`, `max_tool_calls`, `max_turns`, `max_age_days`) and
the `conversation` agent in `llm.yaml` are already accepted; see
[Configuration System](../configuration/system.md).