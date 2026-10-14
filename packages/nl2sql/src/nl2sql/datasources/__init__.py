"""Datasource management and configuration. Adapters are found through ``nl2sql.registry``."""
from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.datasources.models import (
    DatasourceConfig,
    ConnectionConfig,
)
from nl2sql.datasources.protocols import DatasourceAdapter
from nl2sql_sdk.contracts import AdapterRequest, ResultFrame
from nl2sql_sdk.capabilities import DatasourceCapability

__all__ = [
    "DatasourceRegistry", 
    "DatasourceConfig",
    "ConnectionConfig",
    "DatasourceAdapter",
    "AdapterRequest",
    "DatasourceCapability",
    "ResultFrame",
]
