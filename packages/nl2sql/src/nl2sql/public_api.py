"""
Public API for NL2SQL Core Package

This module provides a clean, stable public interface to the NL2SQL core functionality.
It defines the official API boundaries and ensures backward compatibility.
"""

from __future__ import annotations

import pathlib
from typing import Optional, Union
from dataclasses import dataclass

from nl2sql.context import NL2SQLContext
from nl2sql.api.query_api import QueryAPI, QueryResult
from nl2sql.api.datasource_api import DatasourceAPI
from nl2sql.api.llm_api import LLM_API
from nl2sql.api.indexing_api import IndexingAPI
from nl2sql.api.auth_api import AuthAPI
from nl2sql.api.settings_api import SettingsAPI
from nl2sql.api.policy_api import PolicyAPI


class NL2SQL:
    """
    Public API for NL2SQL Core Package

    This class provides a clean, stable interface to the NL2SQL engine functionality.
    It abstracts away the internal implementation details and provides a consistent
    API for external consumers.
    """

    def __init__(
        self,
        ds_config_path: Optional[Union[str, pathlib.Path]] = None,
        secrets_config_path: Optional[Union[str, pathlib.Path]] = None,
        llm_config_path: Optional[Union[str, pathlib.Path]] = None,
        vector_store_path: Optional[Union[str, pathlib.Path]] = None,
        policies_config_path: Optional[Union[str, pathlib.Path]] = None,
        env: Optional[str] = None,
        env_file: Optional[Union[str, pathlib.Path]] = None,
    ):
        """
        Initialize the NL2SQL engine with optional configuration paths.

        Args:
            ds_config_path: Path to datasource configuration file
            secrets_config_path: Path to secrets configuration file
            llm_config_path: Path to LLM configuration file
            vector_store_path: Path to vector store directory
            policies_config_path: Path to policies configuration file
            env: Environment name; loads ``.env.<env>`` from the working directory
            env_file: Path to an env file to load settings from
        """
        ds_config_path = pathlib.Path(ds_config_path) if ds_config_path else None
        secrets_config_path = pathlib.Path(secrets_config_path) if secrets_config_path else None
        llm_config_path = pathlib.Path(llm_config_path) if llm_config_path else None
        vector_store_path = pathlib.Path(vector_store_path) if vector_store_path else None
        policies_config_path = pathlib.Path(policies_config_path) if policies_config_path else None
        env_file = pathlib.Path(env_file) if env_file else None


        self._ctx = NL2SQLContext(
            ds_config_path=ds_config_path,
            secrets_config_path=secrets_config_path,
            llm_config_path=llm_config_path,
            vector_store_path=vector_store_path,
            policies_config_path=policies_config_path,
            env=env,
            env_file=env_file,
        )

        # Initialize modular APIs
        self.query = QueryAPI(self._ctx)
        self.datasource = DatasourceAPI(self._ctx)
        self.llm = LLM_API(self._ctx)
        self.indexing = IndexingAPI(self._ctx)
        self.auth = AuthAPI(self._ctx)
        self.settings = SettingsAPI(self._ctx)
        self.policy = PolicyAPI(self._ctx)
        self._benchmark = None

    @property
    def benchmark(self):
        """The benchmark API, built on first use so ``import nl2sql`` never loads ``nl2sql.evaluation``."""
        if self._benchmark is None:
            from nl2sql.api.benchmark_api import BenchmarkAPI

            self._benchmark = BenchmarkAPI(self._ctx)
        return self._benchmark

    @property
    def context(self) -> NL2SQLContext:
        """Access to the underlying context (internal use only)."""
        return self._ctx

    # Convenience methods that delegate to the modular APIs
    def run_query(
        self,
        natural_language: str,
        datasource_id: Optional[str] = None,
        execute: bool = True,
        user_context=None,
    ):
        """
        Execute a natural language query against the database.
        """
        return self.query.run_query(
            natural_language=natural_language,
            datasource_id=datasource_id,
            execute=execute,
            user_context=user_context
        )

    def add_datasource(self, config):
        """
        Programmatically add a datasource to the engine.
        """
        return self.datasource.add_datasource(config)

    def add_datasource_from_config(self, config_path: Union[str, pathlib.Path]):
        """
        Add datasources from a configuration file.
        """
        return self.datasource.add_datasource_from_config(config_path)

    def list_datasources(self) -> list:
        """
        List all registered datasource IDs.
        """
        return self.datasource.list_datasources()
    
    def get_datasource_capabilities(self, datasource_id: str) -> dict:
        """
        Get capabilities of a specific datasource.
        """
        return self.datasource.get_capabilities(datasource_id)


    def configure_llm(self, config):
        """
        Programmatically configure an LLM.
        """
        return self.llm.configure_llm(config)

    def configure_llm_from_config(self, config_path: Union[str, pathlib.Path]):
        """
        Configure LLMs from a configuration file.
        """
        return self.llm.configure_llm_from_config(config_path)


    def list_llms(self) -> dict:
        """
        List all configured LLMs.
        """
        return self.llm.list_llms()
    
    def get_llm(self, llm_name: str) -> dict:
        """
        Get details of a specific LLM.
        """
        return self.llm.get_llm(llm_name)
    
    def index_datasource(self, datasource_id: str):
        """
        Index schema for a specific datasource.
        """
        return self.indexing.index_datasource(datasource_id)

    def index_all_datasources(self):
        """
        Index schema for all registered datasources.
        """
        return self.indexing.index_all_datasources()

    def clear_index(self):
        """
        Clear the vector store index.
        """
        return self.indexing.clear_index()

    # Auth API convenience methods
    def check_permissions(self, user_context, datasource_id, table):
        """
        Check if a user has permission to access a specific resource.
        """
        return self.auth.check_permissions(user_context, datasource_id, table)

    def get_allowed_resources(self, user_context):
        """
        Get resources a user has access to.
        """
        return self.auth.get_allowed_resources(user_context)

    def get_current_settings(self):
        """
        Get the current application settings.
        """
        return self.settings.get_current_settings()

    def get_setting(self, key):
        """
        Get a specific setting value.
        """
        return self.settings.get_setting(key)

    def validate_configuration(self):
        """
        Validate the current configuration.
        """
        return self.settings.validate_configuration()

    # Schema, index and retrieval: what the playground and nl2sql-api show.
    # Imports are local so ``import nl2sql`` stays light.

    def get_schema(self, datasource_id: str) -> dict:
        """
        The datasource's indexed schema, exactly as the planner is given it.

        Tables sorted by name, each with its columns (type, nullable, primary
        key, description), foreign keys, row count and description. Read from
        the latest schema snapshot, not the database; before the first index
        ``tables`` is empty.
        """
        from nl2sql.schema.view import schema_view

        store = self._ctx.schema_store
        snapshot = store.get_latest_snapshot(datasource_id) if store is not None else None
        return schema_view(datasource_id, snapshot)

    def index_health(self) -> dict:
        """
        Health of the vector index: ``status`` (``ok``, ``empty``, ``stale`` or
        ``missing``), entry counts by type, when it was built, the embedding
        model, one entry per registered datasource and any problems found.
        """
        from nl2sql.indexing.health import IndexHealth, inspect_vector_store

        store = self._ctx.vector_store
        if store is None:
            return IndexHealth(status="missing", problems=["No vector index is configured."]).to_dict()
        registry = self._ctx.ds_registry
        ids = registry.list_ids() if registry is not None else []
        return inspect_vector_store(store, self._ctx.schema_store, ids).to_dict()

    def rebuild_index(self, datasource_ids=None, enrich: bool = False, full: bool = False,
                      on_progress=None, switch_guard=None):
        """
        Rebuild the vector index beside the live one, then switch to it.

        The current entries keep answering questions until the new ones are
        complete. ``enrich`` asks the LLM for descriptions and spends tokens,
        so it is off unless asked for. ``on_progress`` is called with a
        sentence before each step; ``switch_guard`` is a context manager
        factory held around each switch. Returns a ``RebuildResult``
        (``ok``, ``stats``, ``empty``, ``errors``).
        """
        from nl2sql.indexing import rebuild

        return rebuild.rebuild_index(
            self._ctx,
            enrich=enrich,
            datasource_ids=datasource_ids,
            full=full,
            on_progress=on_progress,
            switch_guard=switch_guard,
        )

    def inspect_retrieval(self, query: str, k: int = 8, lambda_mult: Optional[float] = None,
                          types=None, datasource_id: Optional[str] = None) -> dict:
        """
        One MMR search of the live index, as the engine runs it: the candidate
        pool with scores, the picks in order and what was dropped. ``types``
        limits the entry types (``schema.table`` ...). It reads index metadata
        only and needs no LLM.

        Raises:
            LookupError: when no vector index is configured.
        """
        store = self._ctx.vector_store
        if store is None:
            raise LookupError("There is no vector index configured.")
        kwargs = {"lambda_mult": lambda_mult} if lambda_mult is not None else {}
        return store.inspect(query, k=k, types=types, datasource_id=datasource_id, **kwargs)

    def reload_llm_config(self, config_path: Union[str, pathlib.Path]) -> None:
        """
        Replace every configured LLM with those in ``config_path``, as one step.

        Unlike ``configure_llm_from_config`` this also forgets agents the file
        no longer names, so they fall back to ``default``. An invalid file
        leaves the current configuration in place.
        """
        from nl2sql.configs import ConfigManager

        cfg = ConfigManager().load_llm(pathlib.Path(config_path))
        agents = dict(cfg.agents or {})
        agents["default"] = cfg.default
        self._ctx.llm_registry.replace_llms(agents)
