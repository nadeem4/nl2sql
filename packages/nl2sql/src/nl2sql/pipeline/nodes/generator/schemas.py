from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from nl2sql.common.errors import PipelineError


class GeneratorResponse(BaseModel):
    sql_draft: Optional[str] = None
    errors: List[PipelineError] = Field(default_factory=list)
    reasoning: List[Dict[str, Any]] = Field(default_factory=list)
