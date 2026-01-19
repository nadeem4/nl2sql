"""Datasource management, configuration, and adapter discovery."""
from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.datasources.discovery import discover_adapters
from nl2sql.datasources.models import (
    DatasourceConfig, 
    ConnectionConfig,
    QueryResult,
    DryRunResult,
    QueryPlan,
    CostEstimate,
    AdapterError
)
from nl2sql.datasources.protocols import DatasourceAdapter

__all__ = [
    "DatasourceRegistry", 
    "discover_adapters", 
    "DatasourceConfig", 
    "ConnectionConfig",
    "QueryResult",
    "DryRunResult",
    "QueryPlan",
    "CostEstimate",
    "AdapterError",
    "DatasourceAdapter"
]
