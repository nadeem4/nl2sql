    
"""
Datasource API for NL2SQL

Provides functionality for managing datasources programmatically or via config.
"""

from __future__ import annotations

import pathlib
from typing import Union, Dict, Any, List

from nl2sql.config import Datasource
from nl2sql.runtime import Runtime
from nl2sql.datasources.registry import DatasourceRegistry, mask_connection_args


class DatasourceAPI:
    """
    API for managing datasources programmatically or via config files.
    """

    def __init__(self, ctx: Runtime):
        self._ctx = ctx
        self._registry: DatasourceRegistry = ctx.ds_registry

    def add_datasource(self, config: Union[Datasource, Dict[str, Any]]) -> None:
        """
        Add a datasource to the engine: built, not connected, and added to
        ``engine.config``.

        Args:
            config: A ``Datasource``. The 0.4 shape (a dict with
                ``connection.type``) is still read until the REST API moves to
                the ``Datasource`` body.
        """
        if not isinstance(config, Datasource):
            from nl2sql._legacy import datasource_from_04

            config = datasource_from_04(config)
        self._ctx.add_datasource(config)

    def add_datasource_from_config(self, config_path: Union[str, pathlib.Path]) -> None:
        """
        Add the datasources in a 0.4 datasources file.

        Args:
            config_path: Path to the datasource configuration file
        """
        from nl2sql._legacy import datasource_from_04
        from nl2sql.configs import ConfigManager

        for config in ConfigManager().load_datasources(pathlib.Path(config_path)):
            self._ctx.add_datasource(datasource_from_04(config))

    def list_datasources(self) -> List[str]:
        """
        List all registered datasource IDs.
        
        Returns:
            List of datasource IDs
        """
        return self._registry.list_ids()
    
    def get_adapter(self, datasource_id: str):
        """
        Get the adapter for a specific datasource.
        
        Args:
            datasource_id: ID of the datasource
            
        Returns:
            Datasource adapter object
        """
        return self._registry.get_adapter(datasource_id)
    
    def get_capabilities(self, datasource_id: str) -> List[str]:
        """
        Get the capabilities of a specific datasource.
        
        Args:
            datasource_id: ID of the datasource
            
        Returns:
            List of capability strings
        """
        return list(self._registry.get_capabilities(datasource_id))
    

    def validate_connection(
        self,
        ds_id: str,
    ) -> bool:
        """
        Validate a datasource connection configuration without registering it.
        
        Args:
            ds_id: ID of the datasource
            
        Returns:
            True if the connection is valid, raises an exception otherwise
        """
        
        adapter = self._registry.get_adapter(ds_id)
        return adapter.test_connection()
    
    def get_datasource_details(
        self,
        datasource_id: str
    ) -> Dict[str, Any]:
        """
        Get detailed information about a specific datasource.
        
        Args:
            datasource_id: ID of the datasource
            
        Returns:
            Dictionary with datasource details
        """
        adapter = self._registry.get_adapter(datasource_id)
        details = {
            "datasource_id": adapter.datasource_id,
            "datasource_engine_type": adapter.datasource_engine_type,
            "connection_args": mask_connection_args(adapter.connection_args),
            "statement_timeout_ms": adapter.statement_timeout_ms,
            "row_limit": adapter.row_limit,
            "max_bytes": adapter.max_bytes,
            "capabilities": list(self._registry.get_capabilities(datasource_id)),
        }
        return details