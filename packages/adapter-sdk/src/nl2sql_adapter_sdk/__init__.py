"""Adapter SDK: shared contracts for core and adapters."""

from .capabilities import DatasourceCapability
from .contracts import AdapterRequest, ResultColumn, ResultError, ResultFrame
from .protocols import DatasourceAdapterProtocol
from .schema import (
    Column,
    Table,
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
)

__all__ = [
    "DatasourceCapability",
    "AdapterRequest",
    "ResultColumn",
    "ResultError",
    "ResultFrame",
    "DatasourceAdapterProtocol",
    "Column",
    "Table",
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
]
