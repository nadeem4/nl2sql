from typing import Dict, Type, List, Any
import importlib
from nl2sql_adapter_sdk import DatasourceAdapter
from nl2sql.datasources.discovery import discover_adapters
from nl2sql.secrets import secret_manager

class DatasourceRegistry:
    """Manages a collection of active DatasourceAdapters.
    
    Acts as the factory and cache for DataSourceAdapter instances.
    """

    def find_and_resolve_secret(self, key: str) -> str:
        """Attempts to resolve a secret using the secret manager.

        Args:
            key (str): The name of the secret to resolve.

        Returns:
            str: The resolved secret value.

        Raises:
            ValueError: If the secret cannot be resolved.
        """
        return secret_manager.resolve(key)

    def resolved_connection(self, unresolved_connection: dict) -> dict:
        """Resolves all secrets in a connection dictionary.

        Args:
            unresolved_connection (dict): The connection dictionary with potential secrets.

        Returns:
            dict: The connection dictionary with resolved secrets.
        """
        resolved_connection = unresolved_connection.copy()
        for key, value in unresolved_connection.items():
            if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                resolved_connection[key] = self.find_and_resolve_secret(value)
        return resolved_connection

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
                    raise ValueError("Datasource ID is required. Please check your configuration.")
                
                connection = config.get("connection", {})
                conn_type = connection.get("type", "").lower()
                resolved_connection = self.resolved_connection(connection)

                if conn_type in available_adapters:
                    AdapterCls = available_adapters[conn_type]
                    
                    adapter = AdapterCls(
                        datasource_id=ds_id,
                        datasource_engine_type=conn_type,
                        connection_args=resolved_connection,
                        statement_timeout_ms=config.get("statement_timeout_ms"),
                        row_limit=config.get("row_limit"),
                        max_bytes=config.get("max_bytes")
                    )
                    self._adapters[ds_id] = adapter
                else:
                    raise ValueError(f"No adapter found for engine type: '{conn_type}' in datasource '{ds_id}'")
            
            except Exception as e:
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
