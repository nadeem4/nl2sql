"""The ``diagram`` chat tool (docs/architecture/wave_c.md, sections 4 and "Diagrams").

Draws the tables an answer of the conversation read (``nl2sql.diagram_for``), or a datasource's schema as the
conversation's caller may see it (``engine.datasources.diagram``), as Mermaid: no model call. Like every chat tool it
imports only the top-level ``nl2sql`` and its own interface module (D109).
"""
from __future__ import annotations

from typing import ClassVar, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from nl2sql import diagram_for
from nl2sql.chat_tools import ToolContext, ToolNotApplicable, ToolOutput


class DiagramArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: Literal["answer", "schema"] = Field(
        default="answer", description="answer: the tables an answer read; schema: a datasource's tables and relationships.")
    turn: Optional[int] = Field(default=None, ge=1, description="Answer view: the turn's number; the latest turn with an answer when absent.")
    step: Optional[int] = Field(default=None, ge=1, description="Answer view: which of that turn's answers, counting from 1; the first when absent.")
    datasource_id: Optional[str] = Field(default=None, description="Schema view: the datasource to draw.")
    tables: Optional[List[str]] = Field(default=None, description="Schema view: draw these tables and their neighbours; the whole datasource when absent.")


class DiagramTool:
    interface_version: ClassVar[int] = 1
    description: ClassVar[str] = "Draw how the tables of an answer, or of a datasource, are connected, as a Mermaid diagram."
    args_model: ClassVar[type[BaseModel]] = DiagramArgs

    def __call__(self, ctx: ToolContext, args: BaseModel) -> ToolOutput:
        assert isinstance(args, DiagramArgs)
        if args.view == "schema":
            return self._schema(ctx, args)
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
            raise ToolNotApplicable("No answer in this conversation has tables to draw.")
        position = (args.step or 1) - 1
        if position >= len(asks):
            raise ToolNotApplicable(f"Turn {turn.index} has {len(asks)} answer(s); there is no step {args.step}.")
        step = asks[position]
        return ToolOutput(kind="mermaid", title=step.question or "Tables read", body=diagram_for(step.result, dialect_of=ctx.engine.datasources.dialect))

    @staticmethod
    def _schema(ctx: ToolContext, args: DiagramArgs) -> ToolOutput:
        if not args.datasource_id:
            raise ToolNotApplicable("The schema view needs a datasource_id.")
        try:
            text = ctx.engine.datasources.diagram(args.datasource_id, user_context=ctx.user, tables=args.tables)
        except ValueError as error:
            raise ToolNotApplicable(str(error)) from None
        return ToolOutput(kind="mermaid", title=args.datasource_id, body=text)
