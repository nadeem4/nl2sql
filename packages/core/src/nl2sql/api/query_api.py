"""
Query API for NL2SQL

Provides functionality for executing natural language queries against databases.
"""

from __future__ import annotations

from typing import Optional, List
from dataclasses import dataclass

from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.runtime import run_with_graph
from nl2sql.auth import UserContext


@dataclass
class QueryResult:
    """Represents the result of a query execution."""
    sql: Optional[str] = None
    results: list = None
    final_answer: Optional[str] = None
    errors: list = None
    trace_id: Optional[str] = None
    reasoning: List[dict] = None
    warnings: List[dict] = None


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
    ) -> QueryResult:
        """
        Execute a natural language query against the database.
        
        Args:
            natural_language: The natural language query to execute
            datasource_id: Optional specific datasource to query (otherwise auto-resolved)
            execute: Whether to actually execute the SQL against the database
            user_context: Optional user context for permissions
            
        Returns:
            QueryResult containing the results of the query execution
        """
        result_dict = run_with_graph(
            self._ctx,
            natural_language,
            datasource_id=datasource_id,
            execute=execute,
            user_context=user_context
        )
        return QueryResult(**result_dict)