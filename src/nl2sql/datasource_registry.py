from __future__ import annotations

from typing import Dict, Optional
from sqlalchemy.engine import Engine

from nl2sql.datasource_config import DatasourceProfile
from nl2sql.engine_factory import make_engine


class DatasourceRegistry:
    """
    Manages a collection of datasource profiles and their corresponding engines.
    
    This registry allows for lazy loading of engines and centralized management
    of database connections, enabling dynamic routing within the pipeline.
    """

    def __init__(self, profiles: Dict[str, DatasourceProfile]):
        """
        Initializes the registry with a set of profiles.

        Args:
            profiles: A dictionary mapping profile IDs to DatasourceProfile objects.
        """
        self._profiles = profiles
        self._engines: Dict[str, Engine] = {}

    def get_profile(self, datasource_id: str) -> DatasourceProfile:
        """
        Retrieves a datasource profile by ID.

        Args:
            datasource_id: The ID of the datasource.

        Returns:
            The requested DatasourceProfile.

        Raises:
            ValueError: If the datasource ID is unknown.
        """
        if datasource_id not in self._profiles:
            raise ValueError(f"Unknown datasource ID: {datasource_id}")
        return self._profiles[datasource_id]

    def get_engine(self, datasource_id: str) -> Engine:
        """
        Retrieves (or creates) the SQLAlchemy engine for a datasource.

        Args:
            datasource_id: The ID of the datasource.

        Returns:
            The SQLAlchemy Engine.

        Raises:
            ValueError: If the datasource ID is unknown.
        """
        if datasource_id not in self._profiles:
            raise ValueError(f"Unknown datasource ID: {datasource_id}")

        if datasource_id not in self._engines:
            profile = self._profiles[datasource_id]
            self._engines[datasource_id] = make_engine(profile)
        
        return self._engines[datasource_id]

    def list_profiles(self) -> list[DatasourceProfile]:
        """Returns a list of all registered profiles."""
        return list(self._profiles.values())
