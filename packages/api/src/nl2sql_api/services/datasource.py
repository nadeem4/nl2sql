from nl2sql import NL2SQL, Datasource
from nl2sql_api.models.datasource import DatasourceRequest, DatasourceResponse


class DatasourceService:
    def __init__(self, engine: NL2SQL):
        self.engine = engine

    def add_datasource(self, request: DatasourceRequest) -> DatasourceResponse:
        """Add a datasource; ``config`` has the shape of one ``datasources`` entry in ``nl2sql.yaml``."""
        datasource = Datasource.model_validate(request.config)
        self.engine.add_datasource(datasource)
        return DatasourceResponse(
            success=True,
            message=f"Datasource '{datasource.id}' added successfully",
            datasource_id=datasource.id,
        )

    def list_datasources(self) -> list:
        """List all registered datasources."""
        return self.engine.list_datasources()

    def get_datasource(self, datasource_id: str) -> dict:
        """Get details of a specific datasource."""
        datasource_ids = self.engine.list_datasources()
        if datasource_id not in datasource_ids:
            raise ValueError(f"Datasource '{datasource_id}' not found")

        # In a real implementation, we would return detailed information about the datasource
        return {"datasource_id": datasource_id, "exists": True}

    def remove_datasource(self, datasource_id: str) -> dict:
        """Remove a datasource (not directly supported by the engine, but could be implemented)."""
        datasource_ids = self.engine.list_datasources()
        if datasource_id not in datasource_ids:
            raise ValueError(f"Datasource '{datasource_id}' not found")

        return {
            "success": False,
            "message": "Removing datasources is not currently supported by the engine",
            "datasource_id": datasource_id
        }
