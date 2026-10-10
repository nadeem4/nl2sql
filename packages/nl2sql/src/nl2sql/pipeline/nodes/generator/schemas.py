from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from nl2sql.common.errors import PipelineError


class GeneratorResponse(BaseModel):
    sql_draft: Optional[str] = None
    # The adapter's row limit when it, not the plan's own LIMIT, bounds the
    # query: a result with this many rows may have been cut short.
    row_cap: Optional[int] = None
    errors: List[PipelineError] = Field(default_factory=list)
    reasoning: List[Dict[str, Any]] = Field(default_factory=list)
