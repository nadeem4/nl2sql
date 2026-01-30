from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field

from nl2sql.common.errors import PipelineError


class LogicalValidatorResponse(BaseModel):
    errors: List[PipelineError] = Field(default_factory=list)
    reasoning: List[Dict[str, Any]] = Field(default_factory=list)


class PhysicalValidatorResponse(BaseModel):
    errors: List[PipelineError] = Field(default_factory=list)
    reasoning: List[Dict[str, Any]] = Field(default_factory=list)
