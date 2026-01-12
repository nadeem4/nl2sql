from __future__ import annotations
import pathlib
from typing import Optional

from nl2sql.configs import ConfigManager
from nl2sql.datasources import DatasourceRegistry
from nl2sql.services.llm import LLMRegistry
from nl2sql.services.vector_store import OrchestratorVectorStore
from nl2sql.secrets import secret_manager
from nl2sql.common.settings import settings

class NL2SQLContext:
    """
    Centralized application context that manages the initialization lifecycle.
    
    Ensures that secrets are loaded BEFORE datasources, and handles
    registry instantiation in a consistent order.
    """
    
    def __init__(
        self,
        registry: DatasourceRegistry,
        llm_registry: Optional[LLMRegistry],
        vector_store: OrchestratorVectorStore
    ):
        self.registry = registry
        self.llm_registry = llm_registry
        self.vector_store = vector_store

    @classmethod
    def from_paths(
        cls,
        config_path: Optional[pathlib.Path] = None,
        secrets_path: Optional[pathlib.Path] = None,
        llm_config_path: Optional[pathlib.Path] = None,
        vector_store_path: Optional[str] = None,
    ) -> NL2SQLContext:
        """
        Factory method to create a context from configuration paths.
        Resolves defaults from global settings if paths are not provided.
        """
        # 1. Resolve Paths
        if config_path is None:
            config_path = pathlib.Path(settings.datasource_config_path)
        if secrets_path is None:
            secrets_path = pathlib.Path(settings.secrets_config_path)
        if llm_config_path is None:
            llm_config_path = pathlib.Path(settings.llm_config_path)
        if vector_store_path is None:
            vector_store_path = settings.vector_store_path

        cm = ConfigManager()

        # 2. Load Secrets (CRITICAL: Must be first)
        secret_configs = cm.load_secrets(secrets_path)
        if secret_configs:
            secret_manager.configure(secret_configs)

        # 3. Load Datasources
        ds_configs = cm.load_datasources(config_path)
        registry = DatasourceRegistry(ds_configs)

        # 4. Load LLM
        try:
            llm_cfg = cm.load_llm(llm_config_path)
            llm_registry = LLMRegistry(llm_cfg)
        except Exception:
            # LLM might be optional for some operations or fail gracefully
            llm_registry = LLMRegistry(None)

        # 5. Initialize Vector Store
        v_store = OrchestratorVectorStore(persist_directory=vector_store_path)

        return cls(
            registry=registry,
            llm_registry=llm_registry,
            vector_store=v_store
        )
