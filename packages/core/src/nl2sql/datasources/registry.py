from typing import Dict, Type, List, Any, Union
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
        from pydantic import SecretStr
        
        resolved_connection = unresolved_connection.copy()
        for key, value in unresolved_connection.items():
            if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                # Wrap the resolved secret in SecretStr for safety
                secret_val = self.find_and_resolve_secret(value)
                resolved_connection[key] = SecretStr(secret_val)
        return resolved_connection

    def __init__(self, configs: List[Dict[str, Any]]):
        """Initializes the registry by eagerly creating adapters for all configs.

        Args:
            configs: List of datasource configuration objects (Dict or DatasourceConfig).
        """
        self._adapters: Dict[str, DatasourceAdapter] = {}
        self._available_adapters = discover_adapters()

        for config in configs:
            try:
                self.register_datasource(config)
            except Exception as e:
                # Log usage would be better here, but we raise to stop startup on bad config
                raise ValueError(f"Failed to initialize adapter for '{config.get('id', 'unknown')}': {e}") from e

    def register_datasource(self, config: Union[Dict[str, Any], Any]) -> DatasourceAdapter:
        """Registers a new datasource dynamically.

        Args:
            config: The datasource configuration dictionary or object.

        Returns:
            DatasourceAdapter: The created and registered adapter.

        Raises:
            ValueError: If configuration is invalid or adapter type is unknown.
        """
        # Normalize Pydantic Model to Dict
        if hasattr(config, "model_dump"):
            config = config.model_dump()

        ds_id = config.get("id")
        if not ds_id:
            raise ValueError("Datasource ID is required. Please check your configuration.")

        connection = config.get("connection", {})
        conn_type = connection.get("type", "").lower()
        resolved_connection = self.resolved_connection(connection)

        if conn_type in self._available_adapters:
            adapter_cls = self._available_adapters[conn_type]

            adapter = adapter_cls(
                datasource_id=ds_id,
                datasource_engine_type=conn_type,
                connection_args=resolved_connection,
                statement_timeout_ms=config.get("statement_timeout_ms"),
                row_limit=config.get("row_limit"),
                max_bytes=config.get("max_bytes"),
            )
            self._adapters[ds_id] = adapter
            return adapter
        else:
            raise ValueError(
                f"No adapter found for engine type: '{conn_type}' in datasource '{ds_id}'"
            )

    def refresh_schema(self, datasource_id: str, vector_store: Any) -> Dict[str, int]:
        """Refreshes the schema for a specific datasource.
        
        This triggers a fresh intrusion of the database schema via the adapter
        and updates the vector store index.

        Args:
            datasource_id: The ID of the datasource to refresh.
            vector_store: The OrchestratorVectorStore instance.

        Returns:
            Dict[str, int]: Statistics of the refreshed components.

        Raises:
            ValueError: If the datasource ID is unknown.
        """
        adapter = self.get_adapter(datasource_id)
        return vector_store.refresh_schema(adapter, datasource_id)

    def get_adapter(self, datasource_id: str) -> DatasourceAdapter:
        """Retrieves the DataSourceAdapter for a datasource.

        Args:
            datasource_id: The ID of the datasource.

        Returns:
            DatasourceAdapter: The active adapter instance.

        Raises:
            ValueError: If the datasource ID is unknown.
        """
        if datasource_id not in self._adapters:
            raise ValueError(f"Unknown datasource ID: {datasource_id}")
        return self._adapters[datasource_id]

    def get_dialect(self, datasource_id: str) -> str:
        """Returns a normalized dialect string from the adapter.

        Args:
            datasource_id: The ID of the datasource.

        Returns:
            str: The dialect string (e.g., 'postgres').
        """
        return self.get_adapter(datasource_id).get_dialect()

    def list_adapters(self) -> List[DatasourceAdapter]:
        """Returns a list of all registered adapters.

        Returns:
            List[DatasourceAdapter]: All active adapters.
        """
        return list(self._adapters.values())

    def list_ids(self) -> List[str]:
        """Returns a list of all registered datasource IDs.

        Returns:
            List[str]: All registered IDs.
        """
        return list(self._adapters.keys())
