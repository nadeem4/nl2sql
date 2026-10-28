"""Chat tools: what a conversation can do with an answer besides show it (docs/architecture/wave_c.md, section 4).

A chat tool turns something the conversation already has into something else: a chart, a
diagram, a report, a change to a dashboard. The model chooses tools in a bounded loop (D108),
or a client calls one by name. A tool makes no model call and reaches the engine through the
public ``NL2SQL`` facade only (D109); ``ask`` is the conversation's own tool, not a registry
tool. Register one under the ``nl2sql.chat_tools`` entry point group, or with
``registry.add_chat_tool`` / ``@registry.chat_tool``; its interface is version 1.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from nl2sql.common.strict import StrictModel
from nl2sql.conversations.models import Conversation, ToolOutput
from nl2sql_sdk.policy import UserContext


class ToolContext(BaseModel):
    """What a tool is given: the conversation (a copy), the caller, and the public facade."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="forbid")

    conversation: Conversation  # a copy; the tool cannot change it
    user: UserContext
    engine: Any  # the NL2SQL facade; a tool uses its public methods only (D109)


class ToolNotApplicable(Exception):
    """Raised by a tool that cannot apply to that turn; the message says why."""


@runtime_checkable
class ChatTool(Protocol):
    """A chat tool. Built with no arguments: it has no config of its own in 0.9."""

    interface_version: ClassVar[int]  # 1
    description: ClassVar[str]  # one line; the model reads it
    args_model: ClassVar[type[BaseModel]]  # its JSON Schema is shown to the model

    def __call__(self, ctx: ToolContext, args: BaseModel) -> ToolOutput: ...


class ChatToolInfo(StrictModel):
    """One enabled tool, as ``engine.chat_tools.list()`` reports it."""

    name: str
    description: str
    args_schema: Dict[str, Any]


__all__ = ["ChatTool", "ChatToolInfo", "ToolContext", "ToolNotApplicable", "ToolOutput"]
