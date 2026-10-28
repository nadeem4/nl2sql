"""The ``report`` chat tool (docs/architecture/wave_c.md, section 4).

A stub with its final shape: C2 fills it. Like every chat tool it imports only the top-level ``nl2sql``
and its own interface module (D109).
"""
from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from nl2sql.chat_tools import ToolContext, ToolOutput


class ReportArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportTool:
    interface_version: ClassVar[int] = 1
    description: ClassVar[str] = "Write the conversation's answers up as a Markdown report."
    args_model: ClassVar[type[BaseModel]] = ReportArgs

    def __call__(self, ctx: ToolContext, args: BaseModel) -> ToolOutput:
        raise NotImplementedError("The report chat tool arrives with C2 (docs/architecture/wave_c.md, row C2).")
