"""The ``chart`` chat tool (docs/architecture/wave_c.md, sections 4 and 5).

Draws the rows of an ``ask`` step of the conversation as a line, a bar, a big number or a table, through
``engine.results.chart``: no model call. Like every chat tool it imports only the top-level ``nl2sql`` and
its own interface module (D109).
"""
from __future__ import annotations

from typing import ClassVar, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from nl2sql import ResultNotFound
from nl2sql.chat_tools import ToolContext, ToolNotApplicable, ToolOutput


class ChartArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn: Optional[int] = Field(default=None, ge=1, description="The turn's number; the latest turn with an answer when absent.")
    step: Optional[int] = Field(default=None, ge=1, description="Which of that turn's answers, counting from 1; the first when absent.")
    result_id: Optional[str] = Field(default=None, description="A saved result's node id, when the answer saved several.")
    kind: Optional[Literal["line", "bar", "big_number", "table"]] = Field(
        default=None, description="Force a kind the data can draw; chosen from the data when absent.")


class ChartTool:
    interface_version: ClassVar[int] = 1
    description: ClassVar[str] = "Draw the data of an answer as a chart: a line, a bar, a big number or a table."
    args_model: ClassVar[type[BaseModel]] = ChartArgs

    def __call__(self, ctx: ToolContext, args: BaseModel) -> ToolOutput:
        assert isinstance(args, ChartArgs)
        turns = ctx.conversation.turns
        if args.turn is not None:
            chosen = [t for t in turns if t.index == args.turn]
            if not chosen:
                raise ToolNotApplicable(f"There is no turn {args.turn} in this conversation.")
        else:
            chosen = list(reversed(turns))
        for turn in chosen:
            asks = [s for s in turn.steps if s.call.name == "ask" and s.result is not None]
            if asks:
                break
        else:
            raise ToolNotApplicable("No answer in this conversation has data to chart.")
        position = (args.step or 1) - 1
        if position >= len(asks):
            raise ToolNotApplicable(f"Turn {turn.index} has {len(asks)} answer(s) with data; there is no step {args.step}.")
        step = asks[position]
        try:
            # The engine's own tenant: a run saves its results there, whoever asked.
            spec = ctx.engine.results.chart(step.result, args.result_id, kind=args.kind)
        except ResultNotFound:
            raise ToolNotApplicable("The rows of that answer are no longer available, so it cannot be charted.") from None
        except ValueError as error:
            raise ToolNotApplicable(str(error)) from None
        spec = spec.model_copy(update={"title": step.question or spec.title})
        return ToolOutput(kind="chart", title=spec.title, body=spec.model_dump(mode="json"))
