"""The ``chart`` chat tool (docs/architecture/wave_c.md, section 4 and 5).

A stub with its final shape: K1 fills it. Like every chat tool it imports only the top-level ``nl2sql``
and its own interface module (D109).
"""
from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from nl2sql.chat_tools import ToolContext, ToolOutput


class ChartArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChartTool:
    interface_version: ClassVar[int] = 1
    description: ClassVar[str] = "Draw the data of an answer as a chart: a line, a bar, a big number or a table."
    args_model: ClassVar[type[BaseModel]] = ChartArgs

    def __call__(self, ctx: ToolContext, args: BaseModel) -> ToolOutput:
        raise NotImplementedError("The chart chat tool arrives with K1 (docs/architecture/wave_c.md, row K1).")
