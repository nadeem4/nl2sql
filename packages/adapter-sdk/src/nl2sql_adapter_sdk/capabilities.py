from enum import Enum


class DatasourceCapability(str, Enum):
    """Capability flags for datasource adapters."""

    SUPPORTS_SQL = "supports_sql"
    SUPPORTS_REST = "supports_rest"
    SUPPORTS_GRAPHQL = "supports_graphql"
    SUPPORTS_LAKE = "supports_lake"
    SUPPORTS_SCHEMA_INTROSPECTION = "supports_schema_introspection"
    SUPPORTS_DRY_RUN = "supports_dry_run"
    SUPPORTS_COST_ESTIMATE = "supports_cost_estimate"
