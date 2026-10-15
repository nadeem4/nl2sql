from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ResolvedDatasource(BaseModel):
    datasource_id: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # What the decomposer routes on: the description and up to
    # settings.retrieval.decomposer_tables table names (sorted), with the rest counted.
    description: str = ""
    tables: List[str] = Field(default_factory=list)
    more_tables: int = 0
    schema_version: Optional[str] = None


class AnswerabilityResponse(BaseModel):
    """The answerability check's verdict. An empty list means no datasource can answer."""

    answerable_datasource_ids: List[str] = Field(
        default_factory=list,
        description="Ids of the datasources that could answer the question. Empty only if none can.",
    )
    reason: str = Field(default="", description="One short sentence explaining the verdict.")


class DatasourceResolverResponse(BaseModel):
    resolved_datasources: List[ResolvedDatasource] = Field(default_factory=list)
    allowed_datasource_ids: List[str] = Field(default_factory=list)
    unsupported_datasource_ids: List[str] = Field(default_factory=list)


