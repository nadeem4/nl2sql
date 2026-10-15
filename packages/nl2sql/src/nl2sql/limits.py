"""Per-question limits: ``RunStop``, why a run was stopped.

``QueryResult.stop`` holds one when a run ended ``stopped``. The budget that
enforces ``settings.pipeline.max_llm_calls`` and ``max_cost_usd`` (a
run-scoped callback attached by ``RunScope``) arrives in this module with E3;
see docs/architecture/wave_a.md, section 5, "Per-question caps".
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Limit = Literal["max_cost_usd", "max_llm_calls"]


class RunStop(BaseModel):
    """A run stopped at a limit: which one, its configured ``value``, and what was ``spent`` when it stopped."""

    limit: Limit
    value: float
    spent: float
