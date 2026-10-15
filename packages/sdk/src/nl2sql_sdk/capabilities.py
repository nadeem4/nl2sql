from enum import Enum


class DatasourceCapability(str, Enum):
    """Capability flags for datasource adapters.

    Declare one only when it holds on a real server: the conformance kit
    checks each declared flag.
    """

    SUPPORTS_SQL = "supports_sql"
    # The adapter binds ``AdapterRequest.parameters`` by name to ``:name``
    # placeholders in the SQL and never pastes a value into the SQL text.
    # A verified query with parameters runs only on an adapter declaring it.
    SUPPORTS_BOUND_PARAMETERS = "supports_bound_parameters"
    # The session cannot write: an INSERT or CREATE through ``execute`` fails.
    ENFORCES_READ_ONLY = "enforces_read_only"
    # A statement running past ``limits.timeout_ms`` is stopped by the server or the driver.
    ENFORCES_TIMEOUT = "enforces_timeout"
    # ``estimate`` returns the database's estimate of the rows a query reads, without running it
    # (EstimatingAdapterProtocol). The conformance kit checks it.
    ESTIMATES_COST = "estimates_cost"
