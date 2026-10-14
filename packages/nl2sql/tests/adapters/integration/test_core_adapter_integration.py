from unittest.mock import MagicMock

from pydantic import SecretStr

from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.config import Datasource, Limits
from nl2sql.secrets.manager import SecretManager
from nl2sql_sdk.contracts import AdapterRequest, ResultFrame
from nl2sql.adapters.sqlite.adapter import SqliteAdapter
from nl2sql.testing import adapter_registry


def test_core_registry_registers_adapter_and_executes_contract(monkeypatch):
    # Validates core↔adapter integration because registry powers runtime selection.
    # Arrange
    registry = DatasourceRegistry(SecretManager(), adapter_registry({"sqlite": SqliteAdapter}))
    config = Datasource(
        id="ds1",
        type="sqlite", connection=dict(database=":memory:"),
    )

    # Act
    adapter = registry.register_datasource(config)
    response = adapter.execute(AdapterRequest(plan_type="sql", payload={}))

    # Assert
    assert adapter.title == "SQLite"
    assert isinstance(response, ResultFrame)
    assert response.success is False
    assert response.error.error_code == "MISSING_SQL"
