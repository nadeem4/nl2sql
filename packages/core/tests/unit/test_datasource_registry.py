import pytest
from pydantic import SecretStr

from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.datasources.models import DatasourceConfig, ConnectionConfig
from nl2sql.secrets.manager import SecretManager
from nl2sql_adapter_sdk.capabilities import DatasourceCapability


class _StubAdapter:
    def __init__(self, datasource_id, datasource_engine_type, connection_args, **kwargs):
        self.datasource_id = datasource_id
        self.datasource_engine_type = datasource_engine_type
        self.connection_args = connection_args
        self.kwargs = kwargs

    def capabilities(self):
        return {DatasourceCapability.SUPPORTS_SQL, "custom"}

    def get_dialect(self):
        return "stub"


def test_registry_resolves_secrets_and_normalizes_capabilities(monkeypatch):
    # Validates secret resolution and capability normalization because adapters rely on both.
    # Arrange
    monkeypatch.setattr(
        "nl2sql.datasources.registry.discover_adapters",
        lambda: {"stub": _StubAdapter},
    )
    monkeypatch.setenv("SECRET", "resolved")
    registry = DatasourceRegistry(SecretManager())
    config = DatasourceConfig(
        id="ds1",
        connection=ConnectionConfig(type="stub", password="${env:SECRET}"),
        options={"row_limit": 10},
    )

    # Act
    adapter = registry.register_datasource(config)
    caps = registry.get_capabilities("ds1")

    # Assert
    assert isinstance(adapter.connection_args["password"], SecretStr)
    assert adapter.connection_args["password"].get_secret_value() == "resolved"
    assert DatasourceCapability.SUPPORTS_SQL.value in caps
    assert "custom" in caps


def test_registry_rejects_unknown_adapter_type(monkeypatch):
    # Validates fail-fast behavior because misconfigured datasources must not register.
    # Arrange
    monkeypatch.setattr(
        "nl2sql.datasources.registry.discover_adapters",
        lambda: {"stub": _StubAdapter},
    )
    registry = DatasourceRegistry(SecretManager())
    config = DatasourceConfig(id="bad", connection=ConnectionConfig(type="missing"))

    # Act / Assert
    with pytest.raises(ValueError):
        registry.register_datasource(config)
