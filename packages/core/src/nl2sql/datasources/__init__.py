"""Datasource management, configuration, and adapter discovery."""
from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.datasources.discovery import discover_adapters
from nl2sql.datasources.models import (
    DatasourceConfig,
    ConnectionConfig,
)
from nl2sql.datasources.protocols import DatasourceAdapterProtocol
from nl2sql_adapter_sdk.contracts import AdapterRequest, ResultFrame
from nl2sql_adapter_sdk.capabilities import DatasourceCapability

__all__ = [
    "DatasourceRegistry", 
    "discover_adapters", 
    "DatasourceConfig",
    "ConnectionConfig",
    "DatasourceAdapterProtocol",
    "AdapterRequest",
    "DatasourceCapability",
    "ResultFrame",
]
