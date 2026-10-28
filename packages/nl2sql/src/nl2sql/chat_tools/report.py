"""The ``report`` chat tool (docs/architecture/wave_c.md, section 4, "Reports").

Writes the conversation's answered turns up as Markdown through ``engine.conversations.report``: no model call.
Like every chat tool it imports only the top-level ``nl2sql`` and its own interface module (D109).
"""
from __future__ import annotations

from typing import ClassVar, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from nl2sql.chat_tools import ToolContext, ToolNotApplicable, ToolOutput


class ReportArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turns: Optional[List[int]] = Field(default=None, description="The turn numbers to include; every answered turn when absent.")
    title: Optional[str] = Field(default=None, description="The report's title; the conversation's title when absent.")


class ReportTool:
    interface_version: ClassVar[int] = 1
    description: ClassVar[str] = "Write the conversation's answers up as a Markdown report."
    args_model: ClassVar[type[BaseModel]] = ReportArgs

    def __call__(self, ctx: ToolContext, args: BaseModel) -> ToolOutput:
        assert isinstance(args, ReportArgs)
        try:
            markdown = ctx.engine.conversations.report(ctx.conversation.id, user_context=ctx.user,
                                                       turns=args.turns, title=args.title)
        except ValueError as error:
            raise ToolNotApplicable(str(error)) from None
        return ToolOutput(kind="markdown", title=args.title or ctx.conversation.title, body=markdown)
