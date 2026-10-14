"""The nl2sql SDK: the interfaces an extension implements: adapters, secret providers, and (in
``nl2sql_sdk.stores`` and ``nl2sql_sdk.policy``) stores and policy checks."""

from .capabilities import DatasourceCapability
from .contracts import AdapterRequest, ResultError, ResultFrame
from .protocols import DatasourceAdapterProtocol, SqlRenderingAdapterProtocol, StreamingAdapterProtocol
from .secrets import SecretProvider
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
    ColumnRef,
)

__all__ = [
    "DatasourceCapability",
    "AdapterRequest",
    "ResultError",
    "ResultFrame",
    "DatasourceAdapterProtocol",
    "SqlRenderingAdapterProtocol",
    "StreamingAdapterProtocol",
    "SecretProvider",
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
