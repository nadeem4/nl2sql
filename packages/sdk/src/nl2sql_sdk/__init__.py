"""The nl2sql SDK: the interfaces an extension implements: adapters, secret providers, and (in
``nl2sql_sdk.stores``, ``nl2sql_sdk.policy`` and ``nl2sql_sdk.knowledge``) stores, policy checks and
knowledge sources."""

from .capabilities import DatasourceCapability
from .contracts import AdapterRequest, ResultError, ResultFrame
from .models import ConnectionCheck, ConnectionModel, Estimate, IndexScope, Limits, Overlap
from .protocols import (
    DatasourceAdapter,
    EstimatingAdapterProtocol,
    ProfilingAdapterProtocol,
    SqlRenderingAdapterProtocol,
    StreamingAdapterProtocol,
)
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
    IndexIssue,
    ColumnChange,
    ForeignKeyRef,
    SchemaDiff,
)

__all__ = [
    "DatasourceCapability",
    "AdapterRequest",
    "ResultError",
    "ResultFrame",
    "ConnectionCheck",
    "ConnectionModel",
    "IndexScope",
    "Limits",
    "Estimate",
    "Overlap",
    "DatasourceAdapter",
    "SqlRenderingAdapterProtocol",
    "StreamingAdapterProtocol",
    "EstimatingAdapterProtocol",
    "ProfilingAdapterProtocol",
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
    "IndexIssue",
    "ColumnChange",
    "ForeignKeyRef",
    "SchemaDiff",
]
