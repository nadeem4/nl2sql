from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List


class QueryRequest(BaseModel):
    natural_language: str
    datasource_id: Optional[str] = None
    execute: bool = True
    user_context: Optional[Dict[str, Any]] = None


class SubQueryResponse(BaseModel):
    """One decomposed sub-query: its plan, validation checks, SQL and row sample."""
    id: str = ""
    intent: str = ""
    sql: str = ""
    datasource_id: str = ""
    schema_version: str = ""
    plan: Optional[Dict[str, Any]] = None
    validation: List[Dict[str, Any]] = Field(default_factory=list)
    rows: Optional[Dict[str, Any]] = None
    status: str = ""
    retry_count: int = 0


class QueryResponse(BaseModel):
    """Mirrors ``nl2sql.api.query_api.QueryResult``.

    ``sub_queries[].rows`` carries a capped sample only; the full result set
    lives in artifact storage, addressable through ``artifact_refs``.
    """
    sub_queries: List[SubQueryResponse] = Field(default_factory=list)
    final_answer: Optional[Dict[str, Any]] = None
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    trace_id: Optional[str] = None
    reasoning: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[Dict[str, Any]] = Field(default_factory=list)
    artifact_refs: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    status: str = ""
    timings: Dict[str, float] = Field(default_factory=dict)
