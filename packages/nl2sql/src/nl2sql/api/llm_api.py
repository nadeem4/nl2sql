"""
LLM API for NL2SQL

Provides functionality for configuring LLMs programmatically or via config.
"""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, Any, Dict, Optional, Union

from nl2sql.runtime import Runtime
from nl2sql.llm.models import AgentConfig

if TYPE_CHECKING:
    from nl2sql.auth import UserContext
    from nl2sql.config import LLM
    from nl2sql.llm.llm_set import LLMChange


class LLM_API:
    """
    API for configuring LLMs programmatically or via config files.
    """
    
    def __init__(self, ctx: Runtime):
        self._ctx = ctx
        self._registry = ctx.llm_registry

    def replace(self, llm: "LLM", *, by: Optional["UserContext"] = None, via: str = "python") -> "LLMChange":
        """Replace the whole ``llm`` section at once: validated, atomic, logged. Never calls a model.

        ``llm`` is validated as ``llm.yaml`` is, and every agent's API key
        must resolve; an invalid one raises ``ConfigError`` and changes
        nothing. Runs already in flight finish on the old LLM. The change is
        logged (one INFO line) and audited, naming ``by`` (or ``via``, the
        entry point) and each agent's old and new provider and model, never a
        key. It is not written to any file.
        """
        return self._ctx.replace_llm(llm, by=by, via=via)

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
        Replace every agent with the ``llm`` section of an ``nl2sql.yaml``, as one step.

        Agents the section no longer names fall back to the top level. An
        invalid file leaves the current LLMs in place.

        Args:
            config_path: Path to the ``nl2sql.yaml`` whose ``llm`` section to use
        """
        from nl2sql.config import load_config

        self.replace(load_config(config_path).llm)
    
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