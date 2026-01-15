from __future__ import annotations
import pathlib
from typing import Optional

from nl2sql.configs import ConfigManager
from nl2sql.datasources import DatasourceRegistry
from nl2sql.llm import LLMRegistry
from nl2sql.services.vector_store import OrchestratorVectorStore
from nl2sql.secrets import SecretManager
from nl2sql.common.settings import settings
from nl2sql.auth import RBAC

class NL2SQLContext:
    """
    Centralized application context that manages the initialization lifecycle.
    
    Ensures that secrets are loaded BEFORE datasources, and handles
    registry instantiation in a consistent order.
    """

    def __init__(
        self,
        ds_config_path: Optional[pathlib.Path] = None,
        secrets_config_path: Optional[pathlib.Path] = None,
        llm_config_path: Optional[pathlib.Path] = None,
        vector_store_path: Optional[pathlib.Path] = None,
        policies_config_path: Optional[pathlib.Path] = None,
    ) :
        """
        Factory method to create a context from configuration paths.
        Resolves defaults from global settings if paths are not provided.
        """
        ds_config_path = ds_config_path or pathlib.Path(settings.datasource_config_path)
        secrets_config_path = secrets_config_path or pathlib.Path(settings.secrets_config_path)
        llm_config_path = llm_config_path or pathlib.Path(settings.llm_config_path)
        vector_store_path = vector_store_path or pathlib.Path(settings.vector_store_path)
        policies_config_path = policies_config_path or pathlib.Path(settings.policies_config_path)

        cm = ConfigManager()

        secret_configs = cm.load_secrets(secrets_config_path)
        secret_manager = SecretManager()
        if secret_configs:
            secret_manager.configure(secret_configs)

        ds_configs = cm.load_datasources(ds_config_path)
        self.ds_registry = DatasourceRegistry(secret_manager)
        self.ds_registry.register_datasources(ds_configs)

        llm_cfg = cm.load_llm(llm_config_path)
        self.llm_registry = LLMRegistry(secret_manager)
        self.llm_registry.register_llms(llm_cfg)

        self.policies_cfg = cm.load_policies(policies_config_path)
        self.rbac = RBAC(self.policies_cfg)

        self.vector_store = OrchestratorVectorStore(persist_directory=vector_store_path)

       
