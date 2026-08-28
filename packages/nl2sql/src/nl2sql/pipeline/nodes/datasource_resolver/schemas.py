from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ResolvedDatasource(BaseModel):
    datasource_id: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    schema_version: Optional[str] = None
    chunk_schema_version: Optional[str] = None
    schema_version_mismatch: bool = False


class DatasourceResolverResponse(BaseModel):
    resolved_datasources: List[ResolvedDatasource] = Field(default_factory=list)
    allowed_datasource_ids: List[str] = Field(default_factory=list)
    unsupported_datasource_ids: List[str] = Field(default_factory=list)


