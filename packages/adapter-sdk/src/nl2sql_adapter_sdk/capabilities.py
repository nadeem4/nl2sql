from enum import Enum


class DatasourceCapability(str, Enum):
    """Capability flags for datasource adapters."""

    SUPPORTS_SQL = "supports_sql"
    SUPPORTS_REST = "supports_rest"
    # The adapter binds ``AdapterRequest.parameters`` by name to ``:name``
    # placeholders in the SQL and never pastes a value into the SQL text.
    # A verified query with parameters runs only on an adapter declaring it.
    SUPPORTS_BOUND_PARAMETERS = "supports_bound_parameters"
