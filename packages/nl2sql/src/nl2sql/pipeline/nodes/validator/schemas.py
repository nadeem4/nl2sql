from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field

from nl2sql.common.errors import PipelineError


class ValidationCheck(BaseModel):
    """One named validation gate and how it went.

    ``errors`` records what failed; ``checks`` records what was checked at all,
    so a caller can render "structure passed, policy denied" instead of an
    empty list meaning success.
    """

    name: str
    passed: bool
    message: str


class LogicalValidatorResponse(BaseModel):
    errors: List[PipelineError] = Field(default_factory=list)
    reasoning: List[Dict[str, Any]] = Field(default_factory=list)
    checks: List[ValidationCheck] = Field(default_factory=list)
