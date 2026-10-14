import pytest

from nl2sql_sdk.protocols import DatasourceAdapter
from nl2sql.adapters.duckdb.adapter import DuckdbAdapter
from nl2sql.adapters.mssql.adapter import MssqlAdapter
from nl2sql.adapters.mysql.adapter import MysqlAdapter
from nl2sql.adapters.postgres.adapter import PostgresAdapter
from nl2sql.adapters.sqlite.adapter import SqliteAdapter

from ..contract_harness import (
    build_adapter,
    assert_adapter_protocol,
    assert_sql_error_contracts,
    assert_capabilities_include_sql,
)

SERVER = {"host": "localhost", "user": "user", "password": "pw", "database": "db"}

ADAPTER_SPECS = [
    (SqliteAdapter, {"database": ":memory:"}),
    (DuckdbAdapter, {"database": ":memory:"}),
    (PostgresAdapter, SERVER),
    (MysqlAdapter, SERVER),
    (MssqlAdapter, {"host": "localhost", "database": "db"}),
]


@pytest.mark.parametrize("adapter_cls, connection", ADAPTER_SPECS)
def test_adapters_satisfy_protocol_and_error_contracts(adapter_cls, connection):
    # Validates adapter contracts because core assumes consistent behavior across engines.
    # Building never connects, and a refused request never reaches the driver,
    # so no driver or database is needed.
    adapter = build_adapter(adapter_cls, datasource_id="ds1", connection=connection)

    assert_adapter_protocol(adapter)
    assert_capabilities_include_sql(adapter)
    assert_sql_error_contracts(adapter)
    assert isinstance(adapter, DatasourceAdapter)
    assert isinstance(adapter.capabilities(), set)
