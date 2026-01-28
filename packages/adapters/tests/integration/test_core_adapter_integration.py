from unittest.mock import MagicMock

from pydantic import SecretStr

from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.datasources.models import DatasourceConfig, ConnectionConfig
from nl2sql.secrets.manager import SecretManager
from nl2sql_adapter_sdk.contracts import AdapterRequest, ResultFrame
from nl2sql_sqlite.adapter import SqliteAdapter


def test_core_registry_registers_adapter_and_executes_contract(monkeypatch):
    # Validates core↔adapter integration because registry powers runtime selection.
    # Arrange
    monkeypatch.setattr(
        "nl2sql.datasources.registry.discover_adapters",
        lambda: {"sqlite": SqliteAdapter},
    )
    monkeypatch.setattr("nl2sql_sqlite.adapter.create_engine", lambda *a, **k: MagicMock())

    registry = DatasourceRegistry(SecretManager())
    config = DatasourceConfig(
        id="ds1",
        connection=ConnectionConfig(type="sqlite", database=":memory:"),
        options={},
    )

    # Act
    adapter = registry.register_datasource(config)
    response = adapter.execute(AdapterRequest(plan_type="sql", payload={}))

    # Assert
    assert adapter.datasource_engine_type == "sqlite"
    assert isinstance(response, ResultFrame)
    assert response.success is False
    assert response.error.error_code == "MISSING_SQL"
