"""
Indexing API for NL2SQL

Provides functionality for indexing schemas for datasources.
"""

from __future__ import annotations

from typing import Dict, Any

from nl2sql.context import NL2SQLContext
from nl2sql.indexing.orchestrator import IndexingOrchestrator


class IndexingAPI:
    """
    API for indexing schemas for datasources.
    """
    
    def __init__(self, ctx: NL2SQLContext):
        self._ctx = ctx
        self._orchestrator = IndexingOrchestrator(ctx)
    
    def index_datasource(
        self,
        datasource_id: str
    ) -> Dict[str, int]:
        """
        Index schema for a specific datasource.
        
        Args:
            datasource_id: ID of the datasource to index
            
        Returns:
            Dictionary with indexing statistics
        """
        adapter = self._ctx.ds_registry.get_adapter(datasource_id)
        return self._orchestrator.index_datasource(adapter)
    
    def index_all_datasources(self) -> Dict[str, Dict[str, int]]:
        """
        Index schema for all registered datasources.
        
        Returns:
            Dictionary mapping datasource IDs to indexing statistics
        """
        results = {}
        for datasource_id in self._ctx.ds_registry.list_ids():
            try:
                results[datasource_id] = self.index_datasource(datasource_id)
            except Exception as e:
                results[datasource_id] = {"error": str(e)}
        return results
    
    def clear_index(self) -> None:
        """
        Clear the vector store index.
        """
        self._orchestrator.clear_store()