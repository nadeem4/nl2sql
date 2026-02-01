"""
Datasource API for NL2SQL

Provides functionality for managing datasources programmatically or via config.
"""

from __future__ import annotations

import pathlib
from typing import Union, Dict, Any, List

from nl2sql.context import NL2SQLContext
from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.datasources.models import DatasourceConfig


class DatasourceAPI:
    """
    API for managing datasources programmatically or via config files.
    """
    
    def __init__(self, ctx: NL2SQLContext):
        self._ctx = ctx
        self._registry: DatasourceRegistry = ctx.ds_registry
    
    def add_datasource(
        self,
        config: Union[DatasourceConfig, Dict[str, Any]]
    ) -> None:
        """
        Programmatically add a datasource to the engine.
        
        Args:
            config: Datasource configuration as either a DatasourceConfig object
                   or a dictionary with the configuration
        """
        if isinstance(config, dict):
            config = DatasourceConfig(**config)
        
        self._registry.register_datasource(config)
    
    def add_datasource_from_config(
        self,
        config_path: Union[str, pathlib.Path]
    ) -> None:
        """
        Add datasources from a configuration file.
        
        Args:
            config_path: Path to the datasource configuration file
        """
        from nl2sql.configs import ConfigManager
        cm = ConfigManager()
        config_path = pathlib.Path(config_path)
        ds_configs = cm.load_datasources(config_path)
        self._registry.register_datasources(ds_configs)
    
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
            "connection_args": adapter.connection_args,
            "statement_timeout_ms": adapter.statement_timeout_ms,
            "row_limit": adapter.row_limit,
            "max_bytes": adapter.max_bytes,
            "capabilities": list(self._registry.get_capabilities(datasource_id)),
        }
        return details