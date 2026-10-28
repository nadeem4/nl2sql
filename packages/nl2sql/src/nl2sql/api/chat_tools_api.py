"""``engine.chat_tools``: the chat tools a conversation can call (docs/architecture/wave_c.md, section 4)."""
from __future__ import annotations

from typing import List

from nl2sql.chat_tools import ChatToolInfo
from nl2sql.conversations.tools import describe
from nl2sql.runtime import Runtime


class ChatToolsAPI:
    """``engine.chat_tools``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def list(self) -> List[ChatToolInfo]:
        """The enabled chat tools, in the order ``chat_tools:`` names them: name, description and the JSON
        Schema of their arguments. ``ask`` is the conversation's own and is not listed."""
        return describe(self._ctx.chat_tools)
