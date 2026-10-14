import pytest
from sqlalchemy import text

from nl2sql_sdk.contracts import AdapterRequest, ResultFrame
from nl2sql.adapters.duckdb.adapter import DuckdbAdapter, DuckdbConnection

from ..contract_harness import build_adapter


@pytest.fixture
def memory_adapter():
    adapter = build_adapter(
        DuckdbAdapter,
        datasource_id="duck1",
        connection={"database": ":memory:"},
    )
    # execute_sql never commits, so DDL used as test arrangement is written here.
    with adapter.engine.begin() as conn:
        conn.execute(text("CREATE TABLE t AS SELECT 1 AS x"))
    return adapter


def test_url_for_file_database(memory_adapter, tmp_path):
    # Validates URI construction because a wrong prefix silently selects another dialect.
    # Arrange
    db_path = tmp_path / "warehouse.db"

    # Act
    uri = memory_adapter.url(DuckdbConnection(database=str(db_path)))

    # Assert
    assert uri == f"duckdb:///{db_path}"


def test_url_for_in_memory_database(memory_adapter):
    # Validates the in-memory form because it is the default for ad-hoc analysis.
    # Act
    uri = memory_adapter.url(DuckdbConnection(database=":memory:"))

    # Assert
    assert uri == "duckdb:///:memory:"


def test_adapter_connects_and_reports_dialect(memory_adapter):
    # The engine is created on first use.
    # Assert
    assert memory_adapter.engine is not None
    assert memory_adapter.get_dialect() == "duckdb"


def test_execute_sql_round_trips_a_row(memory_adapter):
    # Validates real execution because a mocked engine cannot prove the driver works.
    # Act
    result = memory_adapter.execute(
        AdapterRequest(plan_type="sql", payload={"sql": "SELECT x FROM t"})
    )

    # Assert
    assert isinstance(result, ResultFrame)
    assert result.success is True
    assert result.columns == ["x"]
    assert result.rows == [[1]]
    assert result.row_count == 1
