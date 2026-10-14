from dataclasses import asdict
from typing import Any, Dict, List

from nl2sql import NL2SQL, Datasource
from nl2sql_api.models.datasource import DatasourceRequest, DatasourceResponse


class DatasourceService:
    def __init__(self, engine: NL2SQL):
        self.engine = engine

    def add_datasource(self, request: DatasourceRequest) -> DatasourceResponse:
        """Add a datasource; ``config`` has the shape of one ``datasources`` entry in ``nl2sql.yaml``."""
        datasource = Datasource.model_validate(request.config)
        self.engine.datasources.add(datasource)
        return DatasourceResponse(
            success=True,
            message=f"Datasource '{datasource.id}' added successfully",
            datasource_id=datasource.id,
        )

    def list_datasources(self) -> List[Dict[str, Any]]:
        """Every configured datasource, with its status; connections masked."""
        return [asdict(info) for info in self.engine.datasources.list()]

    def get_datasource(self, datasource_id: str) -> Dict[str, Any]:
        """One datasource, its connection masked; ``ValueError`` for an unknown id."""
        return asdict(self.engine.datasources.get(datasource_id))

    def remove_datasource(self, datasource_id: str) -> Dict[str, Any]:
        """Remove a datasource from the engine; ``ValueError`` for an unknown id."""
        self.engine.datasources.remove(datasource_id)
        return {"success": True, "message": f"Datasource '{datasource_id}' removed", "datasource_id": datasource_id}
