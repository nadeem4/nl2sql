"""Global context management for the NL2SQL pipeline."""
from contextvars import ContextVar
from typing import Optional

current_datasource_id: ContextVar[Optional[str]] = ContextVar("current_datasource_id", default=None)

# The conversation whose turn is running a question; hooks see it as ``HookContext.conversation_id``.
current_conversation_id: ContextVar[Optional[str]] = ContextVar("current_conversation_id", default=None)
