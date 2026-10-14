"""
Indexing API for NL2SQL

Provides functionality for indexing schemas for datasources.
"""

from __future__ import annotations

from typing import Dict, Any

from nl2sql.runtime import Runtime
from nl2sql.indexing.orchestrator import IndexingOrchestrator


class IndexingAPI:
    """
    API for indexing schemas for datasources.
    """
    
    def __init__(self, ctx: Runtime):
        self._ctx = ctx
        self._orchestrator = IndexingOrchestrator(ctx)
    
    def index_datasource(
        self,
        datasource_id: str,
        enrich: bool = False,
    ) -> Dict[str, int]:
        """
        Index schema for a specific datasource.
        
        Args:
            datasource_id: ID of the datasource to index
            enrich: Ask the ``indexing_enrichment`` LLM for descriptions.
                Spends tokens, so it is off unless asked for.
            
        Returns:
            Dictionary with indexing statistics
        """
        adapter = self._ctx.ds_registry.get_adapter(datasource_id)
        orchestrator = IndexingOrchestrator(self._ctx, enrich=enrich)
        return orchestrator.index_datasource(adapter)
    
    def index_all_datasources(self, enrich: bool = False) -> Dict[str, Dict[str, int]]:
        """
        Index schema for all registered datasources.
        
        Args:
            enrich: Ask the ``indexing_enrichment`` LLM for descriptions.
                Spends tokens, so it is off unless asked for.

        Returns:
            Dictionary mapping datasource IDs to indexing statistics
        """
        results = {}
        for datasource_id in self._ctx.ds_registry.list_ids():
            try:
                results[datasource_id] = self.index_datasource(datasource_id, enrich=enrich)
            except Exception as e:
                results[datasource_id] = {"error": str(e)}
        return results
    
    def clear_index(self) -> None:
        """
        Clear the vector store index.
        """
        self._orchestrator.clear_store()