from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from nl2sql_adapter_sdk.protocols import DatasourceAdapterProtocol
from nl2sql_adapter_sdk.capabilities import DatasourceCapability
from nl2sql_mssql.adapter import MssqlAdapter
from nl2sql_mysql.adapter import MysqlAdapter
from nl2sql_postgres.adapter import PostgresAdapter
from nl2sql_sqlite.adapter import SqliteAdapter

from ..contract_harness import (
    build_adapter,
    assert_adapter_protocol,
    assert_sql_error_contracts,
    assert_capabilities_include_sql,
)


ADAPTER_SPECS = [
    (
        "sqlite",
        SqliteAdapter,
        "nl2sql_sqlite.adapter",
        {"type": "sqlite", "database": ":memory:"},
    ),
    (
        "postgres",
        PostgresAdapter,
        "nl2sql_postgres.adapter",
        {
            "type": "postgres",
            "host": "localhost",
            "user": "user",
            "password": SecretStr("pw"),
            "database": "db",
        },
    ),
    (
        "mysql",
        MysqlAdapter,
        "nl2sql_mysql.adapter",
        {
            "type": "mysql",
            "host": "localhost",
            "user": "user",
            "password": SecretStr("pw"),
            "database": "db",
        },
    ),
    (
        "mssql",
        MssqlAdapter,
        "nl2sql_mssql.adapter",
        {"type": "mssql", "host": "localhost", "database": "db"},
    ),
]


@pytest.mark.parametrize("engine_type, adapter_cls, module_path, connection_args", ADAPTER_SPECS)
def test_adapters_satisfy_protocol_and_error_contracts(
    monkeypatch, engine_type, adapter_cls, module_path, connection_args
):
    # Validates adapter contracts because core assumes consistent behavior across engines.
    # Arrange
    monkeypatch.setattr(f"{module_path}.create_engine", lambda *a, **k: MagicMock())
    adapter = build_adapter(
        adapter_cls,
        datasource_id="ds1",
        engine_type=engine_type,
        connection_args=connection_args,
    )

    # Act / Assert
    assert_adapter_protocol(adapter)
    assert_capabilities_include_sql(adapter)
    assert_sql_error_contracts(adapter)
    assert isinstance(adapter, DatasourceAdapterProtocol)
    assert isinstance(adapter.capabilities(), set)
