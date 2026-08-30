from typing import Dict, List, Any, Set
from threading import RLock

from nl2sql_adapter_sdk.capabilities import DatasourceCapability
from nl2sql.datasources.discovery import discover_adapters
from nl2sql.datasources.protocols import DatasourceAdapterProtocol
from nl2sql.secrets import SecretManager
from .models import DatasourceConfig, ConnectionConfig

# Resolved connection args hold plaintext secrets (see ``resolved_connection``),
# so anything that renders them -- an API payload, a `doctor` row, a pydantic
# ValidationError repr -- has to mask them by name first.
SECRET_ARG_HINTS = ("password", "secret", "token", "api_key")


def _is_secret_arg(key: str) -> bool:
    return any(hint in key.lower() for hint in SECRET_ARG_HINTS)


def mask_connection_args(connection_args: Dict[str, Any]) -> Dict[str, Any]:
    """Returns a copy of the connection args with secret values masked."""
    return {
        key: ("***" if _is_secret_arg(key) and value else value)
        for key, value in (connection_args or {}).items()
    }


def redact_connection_secrets(text: str, connection_args: Dict[str, Any]) -> str:
    """Replaces every secret value from the connection args found in ``text``."""
    for key, value in (connection_args or {}).items():
        if _is_secret_arg(key) and isinstance(value, str) and value:
            text = text.replace(value, "***")
    return text


class DatasourceRegistry:
    """Manages a collection of active DatasourceAdapters.
    
    Acts as the factory and cache for DataSourceAdapter instances.
    """

    def __init__(self, secret_manager: SecretManager):
        """Initializes the registry by eagerly creating adapters for all configs."""
        self._adapters: Dict[str, DatasourceAdapterProtocol] = {}
        self._capabilities: Dict[str, Set[str]] = {}
        self._available_adapters = discover_adapters()
        self._secret_manager = secret_manager
        self._lock = RLock()

    def find_and_resolve_secret(self, key: str) -> str:
        """Attempts to resolve a secret using the secret manager.

        Args:
            key (str): The name of the secret to resolve.

        Returns:
            str: The resolved secret value.

        Raises:
            ValueError: If the secret cannot be resolved.
        """
        return self._secret_manager.resolve(key)

    def resolved_connection(self, unresolved_connection: ConnectionConfig) -> ConnectionConfig:
        """Resolves all secrets in a connection dictionary.

        Values are returned as plain strings. ``ConnectionConfig`` declares only
        ``type`` and allows extras, so there is no type information here to tell
        a password apart from a host, a user or a port -- wrapping every
        placeholder in ``SecretStr`` broke every non-password field. Each
        adapter's own config model declares which fields are secret and pydantic
        coerces the plain string into ``SecretStr`` for those, so masking is
        preserved exactly where it is declared.

        Args:
            unresolved_connection (ConnectionConfig): The connection dictionary with potential secrets.

        Returns:
            ConnectionConfig: The connection dictionary with resolved secrets.
        """
        resolved_connection = unresolved_connection.model_dump()
        for key, value in list(resolved_connection.items()):
            if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                resolved_connection[key] = self.find_and_resolve_secret(value)
        return ConnectionConfig(**resolved_connection)

    def register_datasources(self, configs: List[DatasourceConfig]):
        for config in configs:
            try:
                self.register_datasource(config)
            except Exception as e:
                raise ValueError(f"Failed to initialize adapter for '{config.id}': {e}") from e

    def _normalize_capabilities(self, caps: Any) -> Set[str]:
        if not caps:
            return set()
        normalized = set()
        for cap in caps:
            if isinstance(cap, DatasourceCapability):
                normalized.add(cap.value)
            else:
                normalized.add(str(cap))
        return normalized

    def register_datasource(self, config: DatasourceConfig) -> DatasourceAdapterProtocol:
        """Registers a new datasource dynamically.

        Args:
            config: The datasource configuration dictionary or object.

        Returns:
            DatasourceAdapterProtocol: The created and registered adapter.

        Raises:
            ValueError: If configuration is invalid or adapter type is unknown.
        """
        ds_id = config.id
        if not ds_id:
            raise ValueError("Datasource ID is required. Please check your configuration.")

        connection = config.connection
        conn_type = connection.type.lower()
        resolved_connection = self.resolved_connection(connection)
        connection_args = resolved_connection.model_dump()

        if conn_type in self._available_adapters:
            adapter_cls = self._available_adapters[conn_type]

            try:
                adapter = adapter_cls(
                    datasource_id=ds_id,
                    datasource_engine_type=conn_type,
                    connection_args=connection_args,
                    statement_timeout_ms=config.options.get("statement_timeout_ms"),
                    row_limit=config.options.get("row_limit"),
                    max_bytes=config.options.get("max_bytes"),
                )
            except Exception as e:
                # A pydantic ValidationError renders the whole input dict, which
                # now holds the plaintext password. Re-raise without the cause so
                # neither the message nor the traceback carries it.
                raise ValueError(
                    redact_connection_secrets(str(e), connection_args)
                ) from None
            with self._lock:
                self._adapters[ds_id] = adapter
                if hasattr(adapter, "capabilities"):
                    self._capabilities[ds_id] = self._normalize_capabilities(
                        adapter.capabilities()
                    )
                else:
                    self._capabilities[ds_id] = {DatasourceCapability.SUPPORTS_SQL.value}
            return adapter
        else:
            raise ValueError(
                f"No adapter found for engine type: '{conn_type}' in datasource '{ds_id}'"
            )


    def get_adapter(self, datasource_id: str) -> DatasourceAdapterProtocol:
        """Retrieves the adapter for a datasource.

        Args:
            datasource_id: The ID of the datasource.

        Returns:
            DatasourceAdapterProtocol: The active adapter instance.

        Raises:
            ValueError: If the datasource ID is unknown.
        """
        with self._lock:
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

    def get_capabilities(self, datasource_id: str) -> Set[str]:
        """Returns the capability flags for a datasource."""
        with self._lock:
            if datasource_id not in self._capabilities:
                raise ValueError(f"Unknown datasource ID: {datasource_id}")
            return set(self._capabilities[datasource_id])

    def list_adapters(self) -> List[DatasourceAdapterProtocol]:
        """Returns a list of all registered adapters.

        Returns:
            List[DatasourceAdapterProtocol]: All active adapters.
        """
        with self._lock:
            return list(self._adapters.values())

    def list_ids(self) -> List[str]:
        """Returns a list of all registered datasource IDs.

        Returns:
            List[str]: All registered IDs.
        """
        with self._lock:
            return list(self._adapters.keys())
