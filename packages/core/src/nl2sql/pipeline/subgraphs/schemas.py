from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from nl2sql.common.errors import PipelineError
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.execution.contracts import ArtifactRef


class SubgraphOutput(BaseModel):
    subgraph_id: str
    subgraph_name: str
    sub_query_id: Optional[str] = None
    selected_datasource_id: Optional[str] = None
    schema_version: Optional[str] = None
    retry_count: int = 0
    plan: Optional[PlanModel] = None
    sql_draft: Optional[str] = None
    artifact: Optional[ArtifactRef] = None
    errors: List[PipelineError] = Field(default_factory=list)
    reasoning: List[Dict[str, Any]] = Field(default_factory=list)
    status: Optional[str] = None
