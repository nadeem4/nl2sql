"""Core schema models and stores."""

from nl2sql_adapter_sdk.schema import (
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
from .protocol import SchemaStore
from .in_memory_store import SchemaContractStore, SchemaMetadataStore, InMemorySchemaStore
from .sqlite_store import SqliteSchemaStore
from .store import build_schema_store

__all__ = [
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
    "SchemaContractStore",
    "SchemaMetadataStore",
    "SchemaStore",
    "InMemorySchemaStore",
    "SqliteSchemaStore",
    "build_schema_store",
]
