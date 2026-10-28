# Chat tools

!!! note "Wave C, 0.9: the runner, `chart` and `report` are built"
    `diagram` arrives with K2 and `dashboard_edit` and `dashboard_pin` with D4; until then each raises
    `NotImplementedError`, which a conversation reports as a failed step. The design is
    [Wave C, section 4](../architecture/wave_c.md#4-chat-tools).

A chat tool turns something a conversation already has into something else, with no model call of its
own. The five built-ins:

| Tool | Does | Filled by |
|---|---|---|
| `chart` | Draws an answer's data as a line, bar, big number or table ([Charts](../charts/index.md)); args `turn`, `step`, `result_id`, `kind` | K1 (built) |
| `diagram` | Draws the tables of an answer, or of a datasource, as Mermaid ([Diagrams](../charts/diagrams.md)) | K2 |
| `report` | Writes the conversation's answered turns up as Markdown; args `turns`, `title` | C2 (built) |
| `dashboard_edit` | Builds or changes a dashboard ([Dashboards](../dashboards/index.md)) | D4 |
| `dashboard_pin` | Saves the dashboard being built, whole | D4 |

## Which tools are on

`chat_tools:` in `nl2sql.yaml` lists registered names. Absent, the five built-ins are on; `[]` turns every
registry tool off and leaves `ask`, the conversation's own tool, which is always on. A name the registry does
not know is a config error when the engine is built. `engine.chat_tools.list()` returns the enabled tools
(`ChatToolInfo`: name, description, JSON Schema of the arguments); it takes no caller, as it lists names.

## Three ways a tool runs

- **The model calls it.** In a turn the model may call an enabled tool after any result. Its arguments are
  checked against the tool's `args_model` before it runs, and a bad call goes back to the model with what was
  wrong. The tool sees the conversation including the steps already taken in this turn, so a chart of the
  answer just asked for works in one turn. The model gets the step back as a short description (a chart: its
  kind, why and its columns; a diagram: its text; a report or JSON: its title only) and never the data.
  `max_tool_calls` bounds `ask` and tools together.
- **The model offers it.** A reply may carry `offers`, tools it suggests (`Turn.offers`). Nothing runs; a
  client shows a button and, on a click, calls `conversations.tool`.
- **A client calls it by name.** `engine.conversations.tool(cid, "chart", {"turn": 3}, user_context=me)`
  runs one enabled tool as a turn of kind `tool`, with no model call. Arguments that do not validate raise
  `ValueError` and store nothing. A name that is not enabled is stored as a failed step.

## Errors

A step that does not succeed has `status` `error` and `ToolStep.error = {"code", "message"}`; the model sees it
and may go on.

| Code | When |
|---|---|
| `TOOL_FAILED` | The tool raised, or returned something that is not a `ToolOutput`. |
| `TOOL_NOT_APPLICABLE` | The tool raised `ToolNotApplicable(message)`: it cannot apply to that turn (no answer to chart, rows expired). |
| `UNKNOWN_CHAT_TOOL` | The name is not enabled; the message names the enabled tools. |

Every call, successful or not, publishes `chat_tool.called` (`tool`, `conversation_id`, `turn`, `ok`,
`seconds`, `error_code`).

## The report

`engine.conversations.report(cid, user_context=me, turns=None, title=None)` returns Markdown assembled from the
stored turns, with no model call: for each answered question its summary, the plain-English explanation, the
first 20 rows, the takeaway of the chart the data is drawn as (unless it is a table), the SQL in a
`<details>` block and the trace id. Rows are read back through `engine.results`; once they are purged the
section says "Results expired." and keeps the text, the SQL and the trace. `turns` picks turn numbers (an
unknown one is a `ValueError`); the default is every answered turn. A conversation with nothing answered is a
`ValueError`. The `report` tool is the same call, so it covers earlier turns, not the one it is called in.

## Writing one

A tool is a class with `interface_version = 1`, a one-line `description` the model reads, an
`args_model` (a pydantic model that forbids extra keys; its JSON Schema is shown to the model) and
`__call__(ctx: ToolContext, args) -> ToolOutput`. It is built with no arguments. It reaches the engine only
through the public `NL2SQL` facade in `ctx.engine`, and may import only the top-level `nl2sql` and
`nl2sql.chat_tools`. Register it under the `nl2sql.chat_tools` entry point group, or with
`registry.add_chat_tool(name, cls)`, and name it in `chat_tools:` in `nl2sql.yaml`.

```python
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from nl2sql.chat_tools import ToolContext, ToolNotApplicable, ToolOutput


class CountArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuestionCount:
    interface_version: ClassVar[int] = 1
    description: ClassVar[str] = "Say how many questions this conversation has answered."
    args_model: ClassVar[type[BaseModel]] = CountArgs

    def __call__(self, ctx: ToolContext, args: BaseModel) -> ToolOutput:
        asked = sum(s.call.name == "ask" for t in ctx.conversation.turns for s in t.steps)
        if not asked:
            raise ToolNotApplicable("Nothing has been asked yet.")
        return ToolOutput(kind="markdown", title="Questions", body=f"{asked} questions so far.")


registry.add_chat_tool("question_count", QuestionCount)
```

`ToolOutput.kind` is `chart` (a `ChartSpec` as JSON), `mermaid`, `markdown` or `json`; `refs` carries ids a
client needs (`{"dashboard_id": ...}`). A tool makes no model call. `ctx.conversation` is a copy, so a tool
cannot change it.
