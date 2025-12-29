from __future__ import annotations

from typing import Dict, Type
import importlib

from nl2sql.datasources.config import DatasourceProfile
from nl2sql_adapter_sdk import DatasourceAdapter

class DatasourceRegistry:
    """
    Manages a collection of datasource profiles and their corresponding Adapters.
    
    Acts as the factory and cache for DataSourceAdapter instances.
    """

    def __init__(self, profiles: Dict[str, DatasourceProfile]):
        """
        Initializes the registry with a set of profiles.

        Args:
            profiles: A dictionary mapping profile IDs to DatasourceProfile objects.
        """
        self._profiles = profiles
        self._adapters: Dict[str, DataSourceAdapter] = {}

    def get_profile(self, datasource_id: str) -> DatasourceProfile:
        """
        Retrieves a datasource profile by ID.
        """
        if datasource_id not in self._profiles:
            raise ValueError(f"Unknown datasource ID: {datasource_id}")
        return self._profiles[datasource_id]

    def get_adapter(self, datasource_id: str) -> DataSourceAdapter:
        """
        Retrieves (or creates) the DataSourceAdapter for a datasource.

        Args:
            datasource_id: The ID of the datasource.

        Returns:
            An instance complying with the DataSourceAdapter protocol.
        """
        if datasource_id not in self._profiles:
            raise ValueError(f"Unknown datasource ID: {datasource_id}")

        if datasource_id not in self._adapters:
            profile = self._profiles[datasource_id]
            self._adapters[datasource_id] = self._create_adapter(profile)
        
        return self._adapters[datasource_id]

    def _create_adapter(self, profile: DatasourceProfile) -> DataSourceAdapter:
        """
        Factory method to instantiate the correct adapter based on profile engine.
        Now uses dynamic discovery via entry points.
        """
        from nl2sql.datasources.discovery import discover_adapters
        
        available_adapters = discover_adapters()
        engine_type = profile.engine.lower()
        
        if engine_type in available_adapters:
             AdapterCls = available_adapters[engine_type]
             return AdapterCls(profile.sqlalchemy_url)

        raise ValueError(
            f"No adapter found for engine type: '{engine_type}'. "
            f"Available: {list(available_adapters.keys())}. "
            f"Please install the appropriate nl2sql-adapter package."
        )

    def list_profiles(self) -> list[DatasourceProfile]:
        """Returns a list of all registered profiles."""
        return list(self._profiles.values())

