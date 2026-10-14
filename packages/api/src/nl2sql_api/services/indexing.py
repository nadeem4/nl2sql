from typing import Any, Dict, List, Optional

from nl2sql import NL2SQL


class IndexingService:
    def __init__(self, engine: NL2SQL):
        self.engine = engine

    def build(self, datasource_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """Build the index for these datasources (every one when None): ``ok``, ``stats``, ``empty``, ``errors``."""
        result = self.engine.index.build(datasource_ids)
        return {"ok": result.ok, "stats": result.stats, "empty": result.empty, "errors": result.errors}

    def clear_index(self) -> Dict[str, Any]:
        """Clear the vector store index."""
        self.engine.index.clear()
        return {"success": True, "message": "Index cleared successfully"}

    def get_index_status(self) -> Dict[str, Any]:
        """The vector index's health, as ``engine.index.health()`` reports it."""
        return self.engine.index.health()
