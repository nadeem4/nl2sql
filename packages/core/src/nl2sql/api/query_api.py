"""
Query API for NL2SQL

Provides functionality for executing natural language queries against databases.
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field
from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.runtime import run_with_graph
from nl2sql.auth import UserContext
from nl2sql.execution.contracts import ArtifactRef


class SubQueryResult(BaseModel):
    """Represents the result of a sub-query execution."""
    id: str = Field(default="")
    intent: str = Field(default="")
    sql: str = Field(default="")
    query: str = Field(default="")
    datasource_id: str = Field(default="")
    schema_version: str = Field(default="")


class QueryResult(BaseModel):
    """Represents the result of a query execution."""
    sub_queries: List[SubQueryResult] = Field(default_factory=list)
    final_answer: Optional[Dict[str, str]] = None
    errors: List[str] = Field(default_factory=list)
    trace_id: str = Field(default="")
    reasoning: List[dict] = Field(default_factory=list)
    warnings: List[dict] = Field(default_factory=list)
    artifact_refs: Dict[str, ArtifactRef] = Field(default_factory=dict)


class QueryAPI:
    """
    API for executing natural language queries against databases.
    """
    
    def __init__(self, ctx: NL2SQLContext):
        self._ctx = ctx
    
    def run_query(
        self,
        natural_language: str,
        datasource_id: Optional[str] = None,
        execute: bool = True,
        user_context: Optional[UserContext] = None,
    ) -> Dict[str, Any]:
        """
        Execute a natural language query against the database.
        
        Args:
            natural_language: The natural language query to execute
            datasource_id: Optional specific datasource to query (otherwise auto-resolved)
            execute: Whether to actually execute the SQL against the database
            user_context: Optional user context for permissions
            
        Returns:
            Raw graph state dict from the pipeline execution
        """
        result_dict = run_with_graph(
            self._ctx,
            natural_language,
            datasource_id=datasource_id,
            execute=execute,
            user_context=user_context
        )
        return result_dict