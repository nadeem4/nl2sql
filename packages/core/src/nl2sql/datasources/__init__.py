"""Datasource management, configuration, and adapter discovery."""
from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.datasources.config import DatasourceProfile, load_profiles
from nl2sql.datasources.discovery import discover_adapters

__all__ = ["DatasourceRegistry", "DatasourceProfile", "load_profiles", "discover_adapters"]
