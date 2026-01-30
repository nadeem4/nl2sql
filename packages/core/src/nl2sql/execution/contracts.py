from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Literal, Any

from pydantic import BaseModel, Field, ConfigDict

from nl2sql.auth.models import UserContext
from nl2sql.common.errors import PipelineError


ArtifactBackend = Literal["local", "s3", "adls"]
ArtifactFormat = Literal["parquet"]


class ArtifactRef(BaseModel):
    uri: str
    backend: ArtifactBackend
    format: ArtifactFormat
    row_count: int
    columns: List[str]
    bytes: int
    content_hash: str
    created_at: datetime
    schema_version: Optional[str] = None
    path_template: str

    model_config = ConfigDict(extra="ignore")


class ExecutorRequest(BaseModel):
    node_id: str
    trace_id: str
    subgraph_name: str
    datasource_id: Optional[str] = None
    schema_version: Optional[str] = None
    sql: Optional[str] = None
    user_context: Optional[UserContext] = None
    tenant_id: str

    model_config = ConfigDict(extra="ignore")


class ExecutorResponse(BaseModel):
    executor_name: str
    subgraph_name: str
    node_id: str
    trace_id: str
    datasource_id: Optional[str] = None
    schema_version: Optional[str] = None
    artifact: Optional[ArtifactRef] = None
    metrics: Dict[str, float] = Field(default_factory=dict)
    errors: List[PipelineError] = Field(default_factory=list)
    reasoning: List[Dict[str, Any]] = Field(default_factory=list)
    tenant_id: str

    model_config = ConfigDict(extra="ignore")
