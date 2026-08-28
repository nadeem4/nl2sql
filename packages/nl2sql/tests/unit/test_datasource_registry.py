import pytest
from pydantic import SecretStr

from nl2sql.adapters.postgres.adapter import PostgresAdapter, PostgresConnectionConfig
from nl2sql.api.datasource_api import DatasourceAPI
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
    # Resolution hands the adapter a plain string; the adapter's own config model
    # is what declares a field secret. See the PostgresConnectionConfig test below.
    assert adapter.connection_args["password"] == "resolved"
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


def test_resolved_connection_keeps_non_secret_fields_as_plain_strings(monkeypatch):
    # ``ConnectionConfig`` has no per-field types, so resolution must not guess
    # which values are secrets -- the adapter's own model declares that.
    # Arrange
    monkeypatch.setattr(
        "nl2sql.datasources.registry.discover_adapters",
        lambda: {"stub": _StubAdapter},
    )
    monkeypatch.setenv("DEMO_HOST", "manufacturing_ref")
    monkeypatch.setenv("DEMO_USER", "ref_admin")
    monkeypatch.setenv("DEMO_PASSWORD", "ref-pw")
    registry = DatasourceRegistry(SecretManager())
    connection = ConnectionConfig(
        type="stub",
        host="${env:DEMO_HOST}",
        user="${env:DEMO_USER}",
        password="${env:DEMO_PASSWORD}",
    )

    # Act
    resolved = registry.resolved_connection(connection).model_dump()

    # Assert
    assert resolved["host"] == "manufacturing_ref"
    assert resolved["user"] == "ref_admin"
    assert resolved["password"] == "ref-pw"
    assert all(isinstance(resolved[k], str) for k in ("host", "user", "password"))


def test_adapter_config_model_still_masks_the_resolved_password(monkeypatch):
    # The plain string must still land as a ``SecretStr`` on fields declared that
    # way, otherwise dropping the wrap would trade a bug for a leak.
    # Arrange
    monkeypatch.setattr(
        "nl2sql.datasources.registry.discover_adapters",
        lambda: {"stub": _StubAdapter},
    )
    monkeypatch.setenv("DEMO_USER", "ref_admin")
    monkeypatch.setenv("DEMO_PASSWORD", "ref-pw")
    registry = DatasourceRegistry(SecretManager())
    adapter = registry.register_datasource(
        DatasourceConfig(
            id="ds_pg",
            connection=ConnectionConfig(
                type="stub",
                host="manufacturing_ref",
                port=5432,
                user="${env:DEMO_USER}",
                password="${env:DEMO_PASSWORD}",
                database="manufacturing_ref",
            ),
        )
    )

    # Act
    config = PostgresConnectionConfig(**adapter.connection_args)

    # Assert
    assert config.user == "ref_admin"
    assert isinstance(config.password, SecretStr)
    assert config.password.get_secret_value() == "ref-pw"
    assert "ref-pw" not in repr(config)


def test_docker_demo_shaped_datasource_registers(monkeypatch):
    # Regression: every ``DEMO_DOCKER_DATASOURCES`` entry resolves host, port,
    # user and password from the environment, so a wrapped non-password field
    # made the whole Docker demo unregisterable.
    # Arrange
    monkeypatch.setattr(
        "nl2sql.datasources.registry.discover_adapters",
        lambda: {"postgres": PostgresAdapter},
    )
    monkeypatch.setenv("DEMO_REF_HOST", "manufacturing_ref")
    monkeypatch.setenv("DEMO_REF_PORT", "5432")
    monkeypatch.setenv("DEMO_REF_USER", "ref_admin")
    monkeypatch.setenv("DEMO_REF_PASSWORD", "ref-pw")
    registry = DatasourceRegistry(SecretManager())
    config = DatasourceConfig(
        id="manufacturing_ref",
        connection=ConnectionConfig(
            type="postgres",
            host="${env:DEMO_REF_HOST}",
            port="${env:DEMO_REF_PORT}",
            user="${env:DEMO_REF_USER}",
            password="${env:DEMO_REF_PASSWORD}",
            database="manufacturing_ref",
        ),
    )

    # Act
    adapter = registry.register_datasource(config)

    # Assert
    assert adapter.connection_string == (
        "postgresql://ref_admin:ref-pw@manufacturing_ref:5432/manufacturing_ref"
    )


def test_registration_errors_do_not_echo_the_resolved_password(monkeypatch):
    # A pydantic ValidationError renders the whole input dict, and `nl2sql doctor`
    # prints that message verbatim. The plaintext must not survive the trip.
    # Arrange
    monkeypatch.setattr(
        "nl2sql.datasources.registry.discover_adapters",
        lambda: {"postgres": PostgresAdapter},
    )
    monkeypatch.setenv("DEMO_REF_PASSWORD", "s3cret-pw")
    registry = DatasourceRegistry(SecretManager())
    config = DatasourceConfig(
        id="manufacturing_ref",
        connection=ConnectionConfig(
            type="postgres",
            host="manufacturing_ref",
            user="ref_admin",
            password="${env:DEMO_REF_PASSWORD}",
        ),  # `database` is missing on purpose
    )

    # Act
    with pytest.raises(ValueError) as excinfo:
        registry.register_datasource(config)

    # Assert
    assert "s3cret-pw" not in str(excinfo.value)
    assert "s3cret-pw" not in repr(excinfo.value.__cause__)
    assert "database" in str(excinfo.value)


def test_datasource_details_mask_the_resolved_password(monkeypatch):
    # `get_datasource_details` returns the connection args to callers, so the
    # masking that SecretStr used to provide has to be applied explicitly.
    # Arrange
    monkeypatch.setattr(
        "nl2sql.datasources.registry.discover_adapters",
        lambda: {"postgres": PostgresAdapter},
    )
    monkeypatch.setenv("DEMO_REF_PASSWORD", "s3cret-pw")
    registry = DatasourceRegistry(SecretManager())
    registry.register_datasource(
        DatasourceConfig(
            id="ds",
            connection=ConnectionConfig(
                type="postgres",
                host="manufacturing_ref",
                port=5432,
                user="ref_admin",
                password="${env:DEMO_REF_PASSWORD}",
                database="manufacturing_ref",
            ),
        )
    )
    api = DatasourceAPI.__new__(DatasourceAPI)
    api._registry = registry

    # Act
    details = api.get_datasource_details("ds")

    # Assert
    assert details["connection_args"]["password"] == "***"
    assert details["connection_args"]["host"] == "manufacturing_ref"
    assert details["connection_args"]["user"] == "ref_admin"
    # The adapter itself still needs the real value to connect.
    assert registry.get_adapter("ds").connection_args["password"] == "s3cret-pw"
