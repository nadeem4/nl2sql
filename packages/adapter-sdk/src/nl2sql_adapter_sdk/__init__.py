"""Adapter SDK: shared contracts for core and adapters."""

from .capabilities import DatasourceCapability
from .contracts import AdapterRequest, ResultError, ResultFrame
from .protocols import DatasourceAdapterProtocol, SqlRenderingAdapterProtocol
from .schema import (
    TableRef,
    ColumnStatistics,
    ColumnMetadata,
    ColumnContract,
    ForeignKeyContract,
    TableContract,
    TableMetadata,
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    ColumnRef   
)

__all__ = [
    "DatasourceCapability",
    "AdapterRequest",
    "ResultError",
    "ResultFrame",
    "DatasourceAdapterProtocol",
    "SqlRenderingAdapterProtocol",
    "TableRef",
    "ColumnStatistics",
    "ColumnMetadata",
    "ColumnContract",
    "ForeignKeyContract",
    "TableContract",
    "TableMetadata",
    "SchemaContract",
    "SchemaMetadata",
    "SchemaSnapshot",
    "ColumnRef",
]
