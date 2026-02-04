"""
LLM API for NL2SQL

Provides functionality for configuring LLMs programmatically or via config.
"""

from __future__ import annotations

import pathlib
from typing import Union, Dict, Any

from nl2sql.context import NL2SQLContext
from nl2sql.llm.registry import LLMRegistry
from nl2sql.llm.models import AgentConfig


class LLM_API:
    """
    API for configuring LLMs programmatically or via config files.
    """
    
    def __init__(self, ctx: NL2SQLContext):
        self._ctx = ctx
        self._registry = ctx.llm_registry
    
    def configure_llm(
        self,
        config: Union[AgentConfig, Dict[str, Any]]
    ) -> None:
        """
        Programmatically configure an LLM.
        
        Args:
            config: LLM configuration as either an AgentConfig object
                   or a dictionary with the configuration
        """
        if isinstance(config, dict):
            if 'name' not in config:
                config['name'] = 'default'
            config = AgentConfig(**config)
        
        self._registry.register_llm(config)
    
    def configure_llm_from_config(
        self,
        config_path: Union[str, pathlib.Path]
    ) -> None:
        """
        Configure LLMs from a configuration file.
        
        Args:
            config_path: Path to the LLM configuration file
        """
        from nl2sql.configs import ConfigManager
        cm = ConfigManager()
        config_path = pathlib.Path(config_path)
        llm_cfg = cm.load_llm(config_path)
        
        agents = llm_cfg.agents or {}
        agents["default"] = llm_cfg.default
        self._registry.register_llms(agents)
    
    def get_llm(self, name: str):
        """
        Get a specific LLM by name.
        
        Args:
            name: Name of the LLM to retrieve
            
        Returns:
            LLM instance
        """
        return self._registry.get_llm_config(name)
    
    def list_llms(self) -> dict:
        """
        List all configured LLMs.
        
        Returns:
            List of LLM names
        """
        return self._registry.list_llms()