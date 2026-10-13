from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from nl2sql.common.errors import PipelineError
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.execution.contracts import ArtifactRef
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.validator.schemas import ValidationCheck


class SubgraphOutput(BaseModel):
    sub_query: Optional[SubQuery] = None
    subgraph_id: str
    subgraph_name: Optional[str] = None
    retry_count: int = 0
    plan: Optional[PlanModel] = None
    # "cache" when the final plan came from the plan cache, not the planner LLM;
    # "verified" when there was no plan: the SQL is a verified query's.
    plan_source: Literal["llm", "cache", "verified"] = "llm"
    sql_draft: Optional[str] = None
    # The adapter row cap bounding the SQL, when the plan set no smaller LIMIT.
    row_cap: Optional[int] = None
    # True when the result reached ``row_cap``: rows may have been cut off. Exactly
    # ``row_cap`` rows may also be the whole result; that cannot be told apart
    # without fetching more, so it counts as cut.
    truncated: bool = False
    artifact: Optional[ArtifactRef] = None
    errors: List[PipelineError] = Field(default_factory=list)
    validation: List[ValidationCheck] = Field(default_factory=list)
    reasoning: List[Dict[str, Any]] = Field(default_factory=list)
    status: Optional[str] = None
