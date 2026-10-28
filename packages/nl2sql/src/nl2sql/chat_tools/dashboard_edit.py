"""The ``dashboard_edit`` chat tool (docs/architecture/wave_c.md, section 4 and 6).

A stub with its final shape: D4 fills it. Like every chat tool it imports only the top-level ``nl2sql``
and its own interface module (D109).
"""
from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from nl2sql.chat_tools import ToolContext, ToolOutput


class DashboardEditArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DashboardEditTool:
    interface_version: ClassVar[int] = 1
    description: ClassVar[str] = "Build or change a dashboard: add a chart from an answer, rename, rearrange or remove charts."
    args_model: ClassVar[type[BaseModel]] = DashboardEditArgs

    def __call__(self, ctx: ToolContext, args: BaseModel) -> ToolOutput:
        raise NotImplementedError("The dashboard_edit chat tool arrives with D4 (docs/architecture/wave_c.md, row D4).")
