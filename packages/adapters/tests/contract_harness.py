from typing import Any, Dict, Type

from nl2sql_adapter_sdk.protocols import DatasourceAdapterProtocol
from nl2sql_adapter_sdk.capabilities import DatasourceCapability
from nl2sql_adapter_sdk.contracts import AdapterRequest, ResultFrame


def build_adapter(
    adapter_cls: Type[DatasourceAdapterProtocol],
    *,
    datasource_id: str,
    engine_type: str,
    connection_args: Dict[str, Any],
) -> DatasourceAdapterProtocol:
    return adapter_cls(
        datasource_id=datasource_id,
        datasource_engine_type=engine_type,
        connection_args=connection_args,
    )


def assert_adapter_protocol(adapter: DatasourceAdapterProtocol) -> None:
    assert isinstance(adapter, DatasourceAdapterProtocol)
    assert adapter.datasource_id
    assert adapter.datasource_engine_type


def assert_sql_error_contracts(adapter: DatasourceAdapterProtocol) -> None:
    non_sql = adapter.execute(AdapterRequest(plan_type="rest", payload={"sql": "SELECT 1"}))
    missing_sql = adapter.execute(AdapterRequest(plan_type="sql", payload={}))

    assert isinstance(non_sql, ResultFrame)
    assert non_sql.success is False
    assert non_sql.error and non_sql.error.error_code == "CAPABILITY_VIOLATION"

    assert isinstance(missing_sql, ResultFrame)
    assert missing_sql.success is False
    assert missing_sql.error and missing_sql.error.error_code == "MISSING_SQL"


def assert_capabilities_include_sql(adapter: DatasourceAdapterProtocol) -> None:
    caps = {cap.value if isinstance(cap, DatasourceCapability) else str(cap) for cap in adapter.capabilities()}
    assert DatasourceCapability.SUPPORTS_SQL.value in caps
