from typing import Any, Dict, Type

from nl2sql_sdk import Limits
from nl2sql_sdk.protocols import DatasourceAdapter
from nl2sql_sdk.capabilities import DatasourceCapability
from nl2sql_sdk.contracts import AdapterRequest, ResultFrame


def build_adapter(
    adapter_cls: Type[DatasourceAdapter],
    *,
    datasource_id: str,
    connection: Dict[str, Any],
) -> DatasourceAdapter:
    """The adapter as the engine builds it: the connection validated against its model."""
    return adapter_cls(datasource_id, adapter_cls.connection_model(**connection), Limits())


def assert_adapter_protocol(adapter: DatasourceAdapter) -> None:
    assert isinstance(adapter, DatasourceAdapter)
    assert adapter.datasource_id
    assert adapter.interface_version == 2
    assert adapter.title


def assert_sql_error_contracts(adapter: DatasourceAdapter) -> None:
    non_sql = adapter.execute(AdapterRequest(plan_type="rest", payload={"sql": "SELECT 1"}))
    missing_sql = adapter.execute(AdapterRequest(plan_type="sql", payload={}))

    assert isinstance(non_sql, ResultFrame)
    assert non_sql.success is False
    assert non_sql.error and non_sql.error.error_code == "CAPABILITY_VIOLATION"

    assert isinstance(missing_sql, ResultFrame)
    assert missing_sql.success is False
    assert missing_sql.error and missing_sql.error.error_code == "MISSING_SQL"


def assert_capabilities_include_sql(adapter: DatasourceAdapter) -> None:
    caps = {cap.value if isinstance(cap, DatasourceCapability) else str(cap) for cap in adapter.capabilities()}
    assert DatasourceCapability.SUPPORTS_SQL.value in caps
