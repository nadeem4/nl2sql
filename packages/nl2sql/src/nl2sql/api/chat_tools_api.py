"""``engine.chat_tools``: the chat tools a conversation can call (docs/architecture/wave_c.md, section 4).

A stub with its final signature; C2 fills it.
"""
from __future__ import annotations

from typing import List

from nl2sql.chat_tools import ChatToolInfo
from nl2sql.common.planned import arrives_with
from nl2sql.runtime import Runtime


class ChatToolsAPI:
    """``engine.chat_tools``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def list(self) -> List[ChatToolInfo]:
        """The enabled chat tools: name, description and the JSON Schema of their arguments."""
        raise arrives_with("engine.chat_tools.list", "C2")
