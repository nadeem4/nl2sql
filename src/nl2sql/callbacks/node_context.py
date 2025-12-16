import contextvars
from typing import Optional

current_node_run_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_node_run_id", default=None
)

