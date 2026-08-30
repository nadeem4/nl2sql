from __future__ import annotations

from nl2sql.common.errors import PipelineError


class NL2SQLError(Exception):
    """Base exception for all NL2SQL errors."""
    pass


class PipelineExecutionError(NL2SQLError):
    """Raised when a pipeline step fails where a PipelineError cannot be returned.

    Most of the pipeline reports failures by putting a ``PipelineError`` into
    graph state as a value. LangGraph conditional-edge routers cannot do that --
    they may only return routing decisions -- so they raise this instead. The
    structured payload is kept on ``error`` because ``error_code``, ``severity``
    and ``is_retryable`` drive downstream handling; ``str()`` is the underlying
    message so the CLI error decorator prints something readable.
    """

    def __init__(self, error: PipelineError):
        super().__init__(error.message)
        self.error = error
