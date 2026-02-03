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
        """Get the status of the index."""
        # This would typically return information about the vector store
        # For now, we'll return a placeholder
        return {
            "status": "operational",
            "indexed_datasources": self.engine.list_datasources(),
            "total_indexes": len(self.engine.list_datasources())  # Placeholder
        }