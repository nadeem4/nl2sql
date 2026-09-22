from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from nl2sql import QueryResult, SubQueryResult


class QueryRequest(BaseModel):
    natural_language: str
    datasource_id: Optional[str] = None
    execute: bool = True
    user_context: Optional[Dict[str, Any]] = None


class SubQueryResponse(SubQueryResult):
    """One decomposed sub-query: its plan, validation checks, SQL and row sample."""


class QueryResponse(QueryResult):
    """The engine's ``QueryResult``, served as is.

    It derives from ``nl2sql.QueryResult``, so the HTTP response cannot drift
    from what the engine returns. ``sub_queries[].rows`` carries a capped
    sample only; the full result set lives in artifact storage, addressable
    through ``artifact_refs``. ``trace_path`` is where the run's trace was
    written on the server, or None.
    """
    sub_queries: List[SubQueryResponse] = Field(default_factory=list)
