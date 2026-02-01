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
from nl2sql.api.result_api import ResultAPI


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
    ):
        """
        Initialize the NL2SQL engine with optional configuration paths.

        Args:
            ds_config_path: Path to datasource configuration file
            secrets_config_path: Path to secrets configuration file
            llm_config_path: Path to LLM configuration file
            vector_store_path: Path to vector store directory
            policies_config_path: Path to policies configuration file
        """
        # Convert string paths to Path objects if needed
        if ds_config_path:
            ds_config_path = pathlib.Path(ds_config_path)
        if secrets_config_path:
            secrets_config_path = pathlib.Path(secrets_config_path)
        if llm_config_path:
            llm_config_path = pathlib.Path(llm_config_path)
        if vector_store_path:
            vector_store_path = pathlib.Path(vector_store_path)
        if policies_config_path:
            policies_config_path = pathlib.Path(policies_config_path)

        self._ctx = NL2SQLContext(
            ds_config_path=ds_config_path,
            secrets_config_path=secrets_config_path,
            llm_config_path=llm_config_path,
            vector_store_path=vector_store_path,
            policies_config_path=policies_config_path,
        )

        # Initialize modular APIs
        self.query = QueryAPI(self._ctx)
        self.datasource = DatasourceAPI(self._ctx)
        self.llm = LLM_API(self._ctx)
        self.indexing = IndexingAPI(self._ctx)
        self.auth = AuthAPI(self._ctx)
        self.settings = SettingsAPI(self._ctx)
        self.results = ResultAPI(self._ctx)

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

    # Settings API convenience methods
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

    # Result API convenience methods
    def store_query_result(self, frame, metadata=None):
        """
        Store a query result in the result store.
        """
        return self.results.store_query_result(frame, metadata)

    def retrieve_query_result(self, result_id):
        """
        Retrieve a stored query result.
        """
        return self.results.retrieve_query_result(result_id)

    def get_result_metadata(self, result_id):
        """
        Get metadata associated with a stored result.
        """
        return self.results.get_result_metadata(result_id)