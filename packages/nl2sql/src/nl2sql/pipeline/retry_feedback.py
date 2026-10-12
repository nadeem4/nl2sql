"""What the planner and refiner are told on a retry.

``SubgraphExecutionState.errors`` is an additive reducer: it keeps every
attempt's errors, each with its stack trace and details. Sending all of that
back on every retry repeated errors the planner had already fixed, grew the
prompt each time, and put stack traces in front of the model. A retry is told
about the latest attempt only, as ``{node, error_code, message}``.
"""
from __future__ import annotations

import json
from typing import List

from nl2sql.common.errors import ErrorCode, PipelineError


def latest_attempt(errors: List[PipelineError]) -> List[PipelineError]:
    """The errors since the refiner's previous feedback.

    The refiner's feedback (``PLAN_FEEDBACK``) closes an attempt. Read by the
    refiner, the list ends with the failed attempt's errors; read by the
    planner, it ends with the refiner's feedback on them, which is kept.
    """
    end = len(errors)
    if end and errors[-1].error_code == ErrorCode.PLAN_FEEDBACK:
        end -= 1
    start = 0
    for i in range(end - 1, -1, -1):
        if errors[i].error_code == ErrorCode.PLAN_FEEDBACK:
            start = i + 1
            break
    return list(errors[start:])


def _code(error: PipelineError) -> str:
    return getattr(error.error_code, "value", str(error.error_code))


def as_planner_feedback(errors: List[PipelineError]) -> str:
    """One JSON object per line: the latest attempt's ``{node, error_code, message}``."""
    return "\n".join(
        json.dumps({"node": e.node, "error_code": _code(e), "message": e.message}, ensure_ascii=False)
        for e in latest_attempt(errors)
    )


def as_refiner_errors(errors: List[PipelineError]) -> str:
    """One line per error of the latest attempt: ``- [node] CODE: message``."""
    return "\n".join(f"- [{e.node}] {_code(e)}: {e.message}" for e in latest_attempt(errors))
