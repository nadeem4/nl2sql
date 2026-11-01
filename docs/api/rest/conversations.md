# Conversations API

Source: `packages/api/src/nl2sql_api/routes/conversations.py`, `models/conversations.py`, `sse.py`, `overrides.py`.

A thin layer over `engine.conversations` and `engine.chat_tools`: each handler passes the caller as `user_context=` and
serves the facade's own models (`Conversation`, `ConversationInfo`, `Turn`, `ChatToolInfo`; see
[Conversations](../core/conversations.md)). The model decides what a turn does, so there is one `turns` route. A
conversation is the caller's own: another user's is a `404`.

| Route | Does |
|---|---|
| `POST /api/v1/conversations` | Start one, `201` with the `Conversation`. Body `{datasource_id?, dashboard_id?}`; with `dashboard_id` the chat edits that dashboard (`404` if it is not the caller's) |
| `GET /api/v1/conversations` | The caller's conversations, newest first (`limit`, 1 to 100, default 20), as `ConversationInfo[]` |
| `GET /api/v1/conversations/{id}` | The `Conversation`: its turns and, when the last turn asked one, the pending clarification |
| `DELETE /api/v1/conversations/{id}` | Delete it, `204` |
| `POST /api/v1/conversations/{id}/turns` | One turn, the stored `Turn` |
| `POST /api/v1/conversations/{id}/turns/stream` | The same turn as Server-Sent Events |
| `POST /api/v1/conversations/{id}/retry` | Run the last question again as a turn of kind `retry` |
| `POST /api/v1/conversations/{id}/tools/{name}` | Run one chat tool as a turn of kind `tool`, with no model call |
| `GET /api/v1/conversations/{id}/report` | The answered questions as Markdown (`turns` numbers, `title`) |
| `GET /api/v1/chat-tools` | The enabled chat tools: `name`, `description` and the JSON Schema of their arguments (`ask` is not listed) |

## Bodies

Every body is strict: an unknown field is a `422`.

| Body | Fields |
|---|---|
| `TurnRequest` | `text` or `choice` (exactly one), `execute` (default `true`) |
| `RetryRequest` (optional) | `approval_ids`: the granted request ids after a `needs_approval` turn, passed straight to the facade |
| `ToolRequest` (optional) | `args`: the tool's arguments, validated by its `args_model` |

A pipeline outcome is not an error. A turn's `status` is `answered`, `needs_clarification` (answer it with `choice` in
the next turn), `needs_approval`, `stopped` or `error`. A tool that is not enabled is a stored step with an error
`code`, not an error status.

## Statuses

| Status | When |
|---|---|
| `404` | The conversation is not the caller's, or does not exist; on `retry`, also an id in `approval_ids` that is unknown, waiting, denied or expired (`ApprovalNotFound`) |
| `409` | The conversation is already running a turn (`ConversationBusy`) |
| `422` | The body does not validate, or the facade refused an argument (`INVALID_ARGUMENT`: blank text, a `choice` that is not an option, nothing to retry, bad tool arguments, a report with nothing answered; `CONVERSATION_TOO_LONG` at `max_turns`) |

All use the [error envelope](errors.md). The turn routes also take the `X-NL2SQL-*` key and model headers
([Jobs](jobs.md#per-request-keys-and-models)); they apply to the whole turn.

## The stream

`POST .../turns/stream` sends the run events of each question the model asks, a `conversation.step` after each step,
and `conversation.turn` last, whose `data.stored` is the stored `Turn`. A `: keep-alive` comment is sent every 15
seconds. A refusal (not the caller's, a bad body, a busy conversation) answers with the envelope and its status before
the stream begins; once it has, a failure is one last `run.failed` event: a named engine error keeps its `error_code`
and message, any other is `UNKNOWN_ERROR` with a fixed message, never the exception text.

Closing the connection cancels the turn and stores nothing. A step that is running finishes first, so the turn ends
when the step in flight returns.
