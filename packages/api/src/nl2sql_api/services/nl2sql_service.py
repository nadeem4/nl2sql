from typing import Dict, Any, Optional
from nl2sql import NL2SQL, UserContext, QueryResult
from nl2sql_api.models.query import QueryRequest, QueryResponse
from nl2sql_api.models.schema import SchemaResponse

class NL2SQLService:
    def __init__(self):
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            # Initialize with default configuration paths or empty config
            try:
                self._engine = NL2SQL()
            except FileNotFoundError:
                # If config files are not found, initialize with minimal config
                # This allows the API to start but will fail on actual operations
                # without proper configuration
                self._engine = NL2SQL()
        return self._engine

    def execute_query(self, request: QueryRequest) -> QueryResponse:
        # Convert user_context if provided
        user_context = None
        if request.user_context:
            user_context = UserContext(**request.user_context)

        result: QueryResult = self.engine.run_query(
            request.natural_language,
            datasource_id=request.datasource_id,
            execute=request.execute,
            user_context=user_context
        )

        # Map the QueryResult to QueryResponse
        return QueryResponse(
            sql=result.sql,
            results=result.results or [],
            final_answer=result.final_answer,
            errors=result.errors or [],
            trace_id=result.trace_id,
            reasoning=result.reasoning or [],
            warnings=result.warnings or []
        )

    def get_schema(self, datasource_id: str) -> SchemaResponse:
        # Use the indexing API to get schema information
        # The schema information is available through the context
        try:
            # Get the schema snapshot through the engine's context
            snapshot = self.engine.context.schema_store.get_latest_snapshot(datasource_id)

            if not snapshot:
                raise ValueError(f"No schema found for datasource: {datasource_id}")

            # Convert the schema snapshot to the API response format
            tables = []
            relationships = []

            for table_key, table_contract in snapshot.schema.tables.items():
                table_info = {
                    "name": table_key,
                    "columns": [
                        {
                            "name": col.name,
                            "type": col.data_type,
                            "nullable": col.is_nullable,
                            "primary_key": col.is_primary_key
                        }
                        for col in table_contract.columns.values()
                    ],
                    "metadata": table_contract.metadata.dict() if table_contract.metadata else {}
                }
                tables.append(table_info)

                # Extract foreign key relationships
                for fk in table_contract.foreign_keys:
                    relationship = {
                        "table": table_key,
                        "referenced_table": fk.referred_table,
                        "columns": fk.constrained_columns,
                        "referenced_columns": fk.referred_columns
                    }
                    relationships.append(relationship)

            return SchemaResponse(
                datasource_id=datasource_id,
                tables=tables,
                relationships=relationships,
                metadata={
                    "version": snapshot.version,
                    "created_at": snapshot.created_at.isoformat() if hasattr(snapshot, 'created_at') else None,
                    "engine_type": snapshot.schema.engine_type
                }
            )
        except Exception as e:
            raise ValueError(f"Could not retrieve schema for datasource '{datasource_id}': {str(e)}")

    def list_datasources(self) -> list:
        """List all available datasources."""
        return self.engine.list_datasources()