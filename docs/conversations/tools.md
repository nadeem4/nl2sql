# Chat tools

!!! note "Wave C, 0.9: the runner and the five tools are built"
    The design is [Wave C, section 4](../architecture/wave_c.md#4-chat-tools).

A chat tool turns something a conversation already has into something else, with no model call of its
own. The five built-ins:

| Tool | Does | Filled by |
|---|---|---|
| `chart` | Draws an answer's data as a line, bar, big number or table ([Charts](../charts/index.md)); args `turn`, `step`, `result_id`, `kind` | K1 (built) |
| `diagram` | Draws the tables of an answer, or of a datasource, as Mermaid ([Diagrams](../charts/diagrams.md)); args `view` (`answer` or `schema`), `turn`, `step`, `datasource_id`, `tables` | K2 (built) |
| `report` | Writes the conversation's answered turns up as Markdown; args `turns`, `title` | C2 (built) |
| `dashboard_edit` | Builds or changes a dashboard ([Dashboards](../dashboards/index.md#building-in-chat)); args `edits`, `dashboard_id`, `title`, `workspace_id` | D4 (built) |
| `dashboard_pin` | Saves the dashboard being built, whole; args `dashboard_id`, `workspace_id`, `title` | D4 (built) |

## Which tools are on

`chat_tools:` in `nl2sql.yaml` lists registered names. Absent, the default tools are on: `chart`, `diagram`,
`report`, `dashboard_edit` and `dashboard_pin`, those of them the registry has (an engine built on
`Registry.empty()` has none). `[]` turns
every registry tool off and leaves `ask`, the conversation's own tool, which is always on. A name you list
that the registry does not know is a config error when the engine is built, and so is `ask` (it is not a
registry tool). `engine.chat_tools.list()` returns the enabled tools
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

## The dashboard tools

`dashboard_edit` takes `edits`, the list of [dashboard edits](../dashboards/index.md#editing-by-hand) a person can
make by hand plus `add_tile` and `replace_plan`, and applies them with `engine.dashboards.edit`, all or none. Its
other arguments are `dashboard_id` (the conversation's dashboard when absent), and `title` and `workspace_id` for a
new draft. The first call in a conversation makes a **draft** tied to it (`Conversation.dashboard_id`,
`Dashboard.conversation_id`); later calls edit the same one, and a conversation started with
`start(dashboard_id=...)` edits that dashboard. `add_tile` and `replace_plan` name an answer by `from_step`
(`turn`, which defaults to the turn being answered, and which of that turn's `ask` calls with a result, counting
from 1): the engine reads the whole plan from its own
stored step, never from model text, and checks it with a run that executes nothing before saving, so a plan the
validator rejects ends the step as `TOOL_NOT_APPLICABLE` with the validator's message and saves nothing. A draft
this call made is removed again, whatever the failure. If the conversation's dashboard was deleted, the next edit
makes a new draft and the conversation is tied to that. `dashboard_pin` saves the whole draft (`engine.dashboards.pin`) and is called
only when the user asks. A dashboard that is not the caller's is "not found". Neither tool calls a model or
shows the model a row: it gets the step's kind, title and `refs` (`{"dashboard_id": ...}`), and the open
dashboard in `[CONVERSATION]` as `dashboard` (`id`, `title`, `status`, and each chart's `id`, `title`, `question`,
`chart` and last `status`, with the reason for a `needs_fixing` or `denied` one), re-read at every step so it
shows this turn's edits.

## Errors

A step that does not succeed has `status` `error` and `ToolStep.error = {"code", "message"}`; the model sees it
and may go on.

| Code | When |
|---|---|
| `TOOL_FAILED` | The tool raised, or returned something that is not a `ToolOutput`. The message gives the exception type only, since its text can quote data; the full error is in the log. |
| `TOOL_NOT_APPLICABLE` | The tool raised `ToolNotApplicable(message)`: it cannot apply to that turn (no answer to chart, rows expired). |
| `UNKNOWN_CHAT_TOOL` | The name is not enabled; the message names the enabled tools. |

Every call, successful or not, publishes `chat_tool.called` (`tool`, `conversation_id`, `turn`, `ok`,
`seconds`, `error_code`).

## The diagram

`diagram` has two views. `answer` (the default) draws the tables the chosen answer read, from its stored plans,
through `nl2sql.diagram_for`; `turn` picks the turn and `step` which of its answers (the latest turn with an
answer, its first, by default). `schema` draws `datasource_id` through `engine.datasources.diagram` **for the
conversation's caller**, so a table that caller may not read is not drawn; `tables` narrows it. No data is in
either diagram, and a hidden table and a missing one give the same `TOOL_NOT_APPLICABLE`. See
[Diagrams](../charts/diagrams.md).

## The report

`engine.conversations.report(cid, user_context=me, turns=None, title=None)` returns Markdown assembled from the
stored turns, with no model call: for each answered question its summary, the plain-English explanation, the
first 20 rows, the takeaway of the chart the data is drawn as (unless it is a table), the SQL in a
`<details>` block and the trace id. Rows are read back through `engine.results`; once they are purged the
section says "Results expired." and keeps the text, the SQL and the trace. `turns` picks turn numbers (an
unknown one is a `ValueError`); the default is every answered turn. A conversation with nothing answered is a
`ValueError`. Every turn is considered, and a question counts when its `ask` step succeeded, even if a later
tool step in that turn failed. The `report` tool is the same call, so it covers earlier turns, not the one it
is called in. A stored tool output never holds row data (D106): the `report` tool stores the report without
its row tables and chart takeaways, and a stored `chart` output has its spec and columns but `data.rows` is
empty. Ask `conversations.report` again for the full report, and `engine.results.chart` to draw a chart;
both read the rows from the results while their retention keeps them.

## Writing one

A tool is a class with `interface_version = 1`, a one-line `description` the model reads, an
`args_model` (a pydantic model that must forbid extra keys, or the engine refuses it when it is built; its JSON Schema is shown to the model) and
`__call__(ctx: ToolContext, args) -> ToolOutput`. It is built with no arguments. It reaches the engine only
through the public `NL2SQL` facade in `ctx.engine`, and may import only the top-level `nl2sql` and
`nl2sql.chat_tools`. Register it under the `nl2sql.chat_tools` entry point group, or with
`registry.add_chat_tool(name, cls)`, and name it in `chat_tools:` in `nl2sql.yaml`. The name `ask` is taken.

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
