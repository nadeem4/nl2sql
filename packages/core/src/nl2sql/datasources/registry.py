from __future__ import annotations

from typing import Dict, Type, List
import importlib

from nl2sql.datasources.config import DatasourceProfile
from nl2sql_adapter_sdk import DatasourceAdapter

class DatasourceRegistry:
    """Manages a collection of datasource profiles and their corresponding Adapters.
    
    Acts as the factory and cache for DataSourceAdapter instances.

    Attributes:
        _profiles (Dict[str, DatasourceProfile]): Internal storage for profiles.
        _adapters (Dict[str, DatasourceAdapter]): Cache for instantiated adapters.
    """

    def __init__(self, profiles: Dict[str, DatasourceProfile]):
        """Initializes the registry with a set of profiles.

        Args:
            profiles (Dict[str, DatasourceProfile]): A dictionary mapping profile IDs
                to DatasourceProfile objects.
        """
        self._profiles = profiles
        self._adapters: Dict[str, DatasourceAdapter] = {}

    def get_profile(self, datasource_id: str) -> DatasourceProfile:
        """Retrieves a datasource profile by ID.

        Args:
            datasource_id (str): The ID of the datasource.

        Returns:
            DatasourceProfile: The requested profile.

        Raises:
            ValueError: If the datasource ID is unknown.
        """
        if datasource_id not in self._profiles:
            raise ValueError(f"Unknown datasource ID: {datasource_id}")
        return self._profiles[datasource_id]

    def get_adapter(self, datasource_id: str) -> DatasourceAdapter:
        """Retrieves (or creates) the DataSourceAdapter for a datasource.

        Args:
            datasource_id (str): The ID of the datasource.

        Returns:
            DatasourceAdapter: An instance complying with the DataSourceAdapter protocol.
        
        Raises:
            ValueError: If the datasource ID is unknown.
        """
        if datasource_id not in self._profiles:
            raise ValueError(f"Unknown datasource ID: {datasource_id}")

        if datasource_id not in self._adapters:
            profile = self._profiles[datasource_id]
            self._adapters[datasource_id] = self._create_adapter(profile)
        
        return self._adapters[datasource_id]

    def _create_adapter(self, profile: DatasourceProfile) -> DatasourceAdapter:
        """Factory method to instantiate the correct adapter based on profile engine.
        
        Now uses dynamic discovery via entry points.

        Args:
            profile (DatasourceProfile): The datasource profile.

        Returns:
            DatasourceAdapter: The instantiated adapter.

        Raises:
            ValueError: If no adapter is found for the engine type.
        """
        from nl2sql.datasources.discovery import discover_adapters
        
        available_adapters = discover_adapters()
        engine_type = profile.engine.lower()
        
        if engine_type in available_adapters:
            AdapterCls = available_adapters[engine_type]
            return AdapterCls(profile.sqlalchemy_url, datasource_id=profile.id, datasource_engine_type=profile.engine)

        raise ValueError(
            f"No adapter found for engine type: '{engine_type}'. "
            f"Available: {list(available_adapters.keys())}. "
            f"Please install the appropriate nl2sql-adapter package."
        )

    def get_dialect(self, datasource_id: str) -> str:
        """Returns a normalized dialect string suitable for LLMs and SqlGlot.
        
        Normalization rules:
        - mssql, sqlserver -> tsql
        - postgresql -> postgres
        - mysql -> mysql
        - sqlite -> sqlite
        - oracle -> oracle

        Args:
            datasource_id (str): The datasource ID.

        Returns:
            str: The normalized dialect string.
        """
        profile = self.get_profile(datasource_id)
        engine = profile.engine.lower()
        
        if "postgres" in engine:
            return "postgres"
        if "mysql" in engine:
            return "mysql"
        if "sqlite" in engine:
            return "sqlite"
        if "oracle" in engine:
            return "oracle"
        if "mssql" in engine or "sqlserver" in engine:
            return "tsql"
        
        return "tsql" # Default fallback

    def list_profiles(self) -> List[DatasourceProfile]:
        """Returns a list of all registered profiles.
        
        Returns:
            List[DatasourceProfile]: List of all profiles.
        """
        return list(self._profiles.values())
