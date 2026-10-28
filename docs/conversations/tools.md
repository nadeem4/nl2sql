# Chat tools

!!! note "Arrives with C2 (Wave C, 0.9)"
    This page is a stub. The interface (`ChatTool`, `ToolContext`, `ToolOutput`, `ToolNotApplicable`)
    and the five built-in entry points exist; each built-in raises `NotImplementedError` naming the
    pull request that fills it. The design is [Wave C, section 4](../architecture/wave_c.md#4-chat-tools).

A chat tool turns something a conversation already has into something else, with no model call of its
own. The five built-ins:

| Tool | Does | Filled by |
|---|---|---|
| `chart` | Draws an answer's data as a line, bar, big number or table ([Charts](../charts/index.md)); args `turn`, `step`, `result_id`, `kind` | K1 (built) |
| `diagram` | Draws the tables of an answer, or of a datasource, as Mermaid ([Diagrams](../charts/diagrams.md)) | K2 |
| `report` | Writes the conversation up as Markdown | C2 |
| `dashboard_edit` | Builds or changes a dashboard ([Dashboards](../dashboards/index.md)) | D4 |
| `dashboard_pin` | Saves the dashboard being built, whole | D4 |

## Writing one

A tool is a class with `interface_version = 1`, a one-line `description` the model reads, an
`args_model` (a pydantic model whose JSON Schema is shown to the model) and
`__call__(ctx: ToolContext, args) -> ToolOutput`. It reaches the engine only through the public
`NL2SQL` facade in `ctx.engine`. Register it under the `nl2sql.chat_tools` entry point group, or with
`registry.add_chat_tool(name, cls)`, and name it in `chat_tools:` in `nl2sql.yaml`.