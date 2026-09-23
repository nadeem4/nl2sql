from enum import Enum


class DatasourceCapability(str, Enum):
    """Capability flags for datasource adapters."""

    SUPPORTS_SQL = "supports_sql"
    SUPPORTS_REST = "supports_rest"
