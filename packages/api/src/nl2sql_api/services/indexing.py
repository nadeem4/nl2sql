from typing import Dict, Any, Optional
from nl2sql import NL2SQL


class IndexingService:
    def __init__(self, engine: NL2SQL):
        self.engine = engine

    def index_datasource(self, datasource_id: str) -> Dict[str, int]:
        """Index schema for a specific datasource."""
        return self.engine.indexing.index_datasource(datasource_id)

    def index_all_datasources(self) -> Dict[str, Dict[str, int]]:
        """Index schema for all registered datasources."""
        return self.engine.indexing.index_all_datasources()

    def clear_index(self) -> None:
        """Clear the vector store index."""
        self.engine.indexing.clear_index()
        return {"success": True, "message": "Index cleared successfully"}

    def get_index_status(self) -> Dict[str, Any]:
        """The vector index's health, as ``NL2SQL.index_health`` reports it."""
        return self.engine.index_health()