from typing import Dict, Type, List, Any
import importlib
from nl2sql_adapter_sdk import DatasourceAdapter
from nl2sql.datasources.discovery import discover_adapters

class DatasourceRegistry:
    """Manages a collection of active DatasourceAdapters.
    
    Acts as the factory and cache for DataSourceAdapter instances.
    """

    def __init__(self, configs: List[Dict[str, Any]]):
        """Initializes the registry by eagerly creating adapters for all configs.

        Args:
            configs (List[Dict[str, Any]]): List of datasource configuration dictionaries.
        """
        self._adapters: Dict[str, DatasourceAdapter] = {}
        available_adapters = discover_adapters()

        for config in configs:
            try:
                ds_id = config.get("id")
                if not ds_id:
                    continue # Skip invalid configs without ID
                
                # Extract Connection Info
                connection = config.get("connection", {})
                conn_type = connection.get("type", "").lower()
                
                if not conn_type and "type" in config:
                    # Legacy fallback if type is at root
                    conn_type = config["type"].lower()

                if conn_type in available_adapters:
                    AdapterCls = available_adapters[conn_type]
                    
                    # Instantiate Adapter (Eagerly)
                    adapter = AdapterCls(
                        datasource_id=ds_id,
                        datasource_engine_type=conn_type,
                        connection_args=connection,
                        statement_timeout_ms=config.get("statement_timeout_ms"),
                        row_limit=config.get("row_limit"),
                        max_bytes=config.get("max_bytes")
                    )
                    self._adapters[ds_id] = adapter
                else:
                    # Log warning or error? For now, we skip unknown adapters or raise?
                    # Raising strict error to match previous behavior
                    raise ValueError(f"No adapter found for engine type: '{conn_type}' in datasource '{ds_id}'")
            
            except Exception as e:
                # In eager loading, one bad apple crashes the start.
                raise ValueError(f"Failed to initialize adapter for '{config.get('id', 'unknown')}': {e}") from e

    def get_adapter(self, datasource_id: str) -> DatasourceAdapter:
        """Retrieves the DataSourceAdapter for a datasource.

        Args:
            datasource_id (str): The ID of the datasource.

        Returns:
            DatasourceAdapter: The active adapter instance.
        
        Raises:
            ValueError: If the datasource ID is unknown.
        """
        if datasource_id not in self._adapters:
            raise ValueError(f"Unknown datasource ID: {datasource_id}")
        return self._adapters[datasource_id]

    def get_dialect(self, datasource_id: str) -> str:
        """Returns a normalized dialect string from the adapter."""
        return self.get_adapter(datasource_id).get_dialect()

    def list_adapters(self) -> List[DatasourceAdapter]:
        """Returns a list of all registered adapters."""
        return list(self._adapters.values())
