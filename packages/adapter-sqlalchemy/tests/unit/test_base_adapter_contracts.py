from unittest.mock import MagicMock

import pytest

from nl2sql_sqlalchemy_adapter.adapter import BaseSQLAlchemyAdapter
from nl2sql_adapter_sdk.contracts import AdapterRequest


class _TestAdapter(BaseSQLAlchemyAdapter):
    def construct_uri(self, args):
        return "sqlite:///:memory:"

    def explain(self, sql: str):
        raise NotImplementedError

    def cost_estimate(self, sql: str):
        raise NotImplementedError

    def get_dialect(self) -> str:
        return "sqlite"

    @property
    def exclude_schemas(self):
        return set()


def test_execute_rejects_non_sql_requests(monkeypatch):
    # Validates error normalization because callers depend on consistent error codes.
    # Arrange
    monkeypatch.setattr("nl2sql_sqlalchemy_adapter.adapter.create_engine", lambda *a, **k: MagicMock())
    adapter = _TestAdapter(
        datasource_id="ds1",
        datasource_engine_type="sqlite",
        connection_args={},
    )

    # Act
    response = adapter.execute(AdapterRequest(plan_type="rest", payload={"sql": "SELECT 1"}))

    # Assert
    assert response.success is False
    assert response.error.error_code == "CAPABILITY_VIOLATION"


def test_execute_rejects_missing_sql(monkeypatch):
    # Validates missing SQL handling because adapter must fail closed safely.
    # Arrange
    monkeypatch.setattr("nl2sql_sqlalchemy_adapter.adapter.create_engine", lambda *a, **k: MagicMock())
    adapter = _TestAdapter(
        datasource_id="ds1",
        datasource_engine_type="sqlite",
        connection_args={},
    )

    # Act
    response = adapter.execute(AdapterRequest(plan_type="sql", payload={}))

    # Assert
    assert response.success is False
    assert response.error.error_code == "MISSING_SQL"


def test_statement_timeout_sets_execution_options(monkeypatch):
    # Validates timeout wiring because queries must honor SLA limits.
    # Arrange
    monkeypatch.setattr("nl2sql_sqlalchemy_adapter.adapter.create_engine", lambda *a, **k: MagicMock())

    # Act
    adapter = _TestAdapter(
        datasource_id="ds1",
        datasource_engine_type="sqlite",
        connection_args={},
        statement_timeout_ms=1500,
        row_limit=10,
        max_bytes=100,
    )

    # Assert
    assert adapter.execution_options["timeout"] == 1.5
    assert adapter.row_limit == 10
    assert adapter.max_bytes == 100
