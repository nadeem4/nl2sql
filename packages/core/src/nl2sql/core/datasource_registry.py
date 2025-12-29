from __future__ import annotations

from typing import Dict, Type
import importlib

from nl2sql.core.datasource_config import DatasourceProfile
from nl2sql_adapter_sdk import DatasourceAdapter
# from nl2sql.adapters.sql_generic import SqlGenericAdapter
# In a real plugin system, we would use importlib.metadata to find these
# from nl2sql.adapters.adls import AdlsAdapter, etc.

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
        """
        # 1. Naive Mapping (simulating Entry Points for V1)
        # In the future, this dict can be populated via entry_points
        engine_type = profile.engine.lower()
        
        # SQL Family
        if engine_type in ["postgres", "postgresql", "sqlite", "mysql", "mssql", "sqlserver", "oracle"]:
             # return SqlGenericAdapter(profile.sqlalchemy_url)
             raise NotImplementedError("Adapters are being refactored. Please install nl2sql-adapters-sql-generic.")
            
        # Future:
        # if engine_type == "adls": return AdlsAdapter(...)
        
        raise ValueError(f"No adapter found for engine type: {engine_type}")

    def list_profiles(self) -> list[DatasourceProfile]:
        """Returns a list of all registered profiles."""
        return list(self._profiles.values())

