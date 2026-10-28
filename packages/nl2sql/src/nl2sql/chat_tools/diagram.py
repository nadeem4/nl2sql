"""The ``diagram`` chat tool (docs/architecture/wave_c.md, section 4).

A stub with its final shape: K2 fills it. Like every chat tool it imports only the top-level ``nl2sql``
and its own interface module (D109).
"""
from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from nl2sql.chat_tools import ToolContext, ToolOutput


class DiagramArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiagramTool:
    interface_version: ClassVar[int] = 1
    description: ClassVar[str] = "Draw how the tables of an answer, or of a datasource, are connected, as a Mermaid diagram."
    args_model: ClassVar[type[BaseModel]] = DiagramArgs

    def __call__(self, ctx: ToolContext, args: BaseModel) -> ToolOutput:
        raise NotImplementedError("The diagram chat tool arrives with K2 (docs/architecture/wave_c.md, row K2).")
