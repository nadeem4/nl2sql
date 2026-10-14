import pytest
from pydantic import SecretStr

from nl2sql.adapters.postgres.adapter import PostgresAdapter
from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.config import Datasource, Limits
from nl2sql.secrets.manager import SecretManager
from nl2sql_sdk.capabilities import DatasourceCapability
from nl2sql.testing import OpenConnection, adapter_registry


class _StubAdapter:
    interface_version = 2
    title = "Stub"
    connection_model = OpenConnection
    driver_modules = ()
    install_hint = ""

    def __init__(self, datasource_id, connection, limits, *, folder=None):
        self.datasource_id = datasource_id
        self.connection_args = connection.model_dump()

    def capabilities(self):
        return {DatasourceCapability.SUPPORTS_SQL, "custom"}

    def get_dialect(self):
        return "stub"


def test_registry_resolves_secrets_and_normalizes_capabilities(monkeypatch):
    # Validates secret resolution and capability normalization because adapters rely on both.
    # Arrange
    monkeypatch.setenv("SECRET", "resolved")
    registry = DatasourceRegistry(SecretManager(), adapter_registry({"stub": _StubAdapter}))
    config = Datasource(
        id="ds1",
        type="stub", connection=dict(password="${env:SECRET}"),
        limits=Limits(row_limit=10),
    )

    # Act
    adapter = registry.register_datasource(config)
    caps = registry.get_capabilities("ds1")

    # Assert
    # Resolution hands the model a plain string; the adapter's own connection
    # model is what declares a field secret. See the PostgresConnection test below.
    assert adapter.connection_args["password"] == "resolved"
    assert DatasourceCapability.SUPPORTS_SQL.value in caps
    assert "custom" in caps


def test_registry_rejects_unknown_adapter_type(monkeypatch):
    # Validates fail-fast behavior because misconfigured datasources must not register.
    # Arrange
    registry = DatasourceRegistry(SecretManager(), adapter_registry({"stub": _StubAdapter}))
    config = Datasource(id="bad", type="missing")

    # Act / Assert
    with pytest.raises(ValueError):
        registry.register_datasource(config)


def test_resolved_connection_keeps_non_secret_fields_as_plain_strings(monkeypatch):
    # ``connection`` has no per-field types, so resolution must not guess
    # which values are secrets -- the adapter's own model declares that.
    # Arrange
    monkeypatch.setenv("DEMO_HOST", "analytics_db")
    monkeypatch.setenv("DEMO_USER", "ref_admin")
    monkeypatch.setenv("DEMO_PASSWORD", "ref-pw")
    registry = DatasourceRegistry(SecretManager(), adapter_registry({"stub": _StubAdapter}))
    connection = {"host": "${env:DEMO_HOST}", "user": "${env:DEMO_USER}", "password": "${env:DEMO_PASSWORD}"}

    # Act
    resolved = registry.resolved_connection(connection)

    # Assert
    assert resolved["host"] == "analytics_db"
    assert resolved["user"] == "ref_admin"
    assert resolved["password"] == "ref-pw"
    assert all(isinstance(resolved[k], str) for k in ("host", "user", "password"))


def test_adapter_connection_model_still_masks_the_resolved_password(monkeypatch):
    # The plain string must still land as a ``SecretStr`` on fields declared that
    # way, otherwise dropping the wrap would trade a bug for a leak.
    # Arrange
    monkeypatch.setenv("DEMO_USER", "ref_admin")
    monkeypatch.setenv("DEMO_PASSWORD", "ref-pw")
    registry = DatasourceRegistry(SecretManager(), adapter_registry({"postgres": PostgresAdapter}))

    # Act
    adapter = registry.register_datasource(
        Datasource(
            id="ds_pg",
            type="postgres", connection=dict(host="analytics_db",
                port=5432,
                user="${env:DEMO_USER}",
                password="${env:DEMO_PASSWORD}",
                database="analytics_db",
            ),
        )
    )
    config = adapter.connection

    # Assert
    assert config.user == "ref_admin"
    assert isinstance(config.password, SecretStr)
    assert config.password.get_secret_value() == "ref-pw"
    assert "ref-pw" not in repr(config)


def test_fully_env_resolved_datasource_registers(monkeypatch):
    # Regression: a datasource that resolves host, port, user *and* password
    # from the environment was unregisterable, because a wrapped non-password
    # field was not unwrapped.
    # Arrange
    monkeypatch.setenv("DEMO_REF_HOST", "analytics_db")
    monkeypatch.setenv("DEMO_REF_PORT", "5432")
    monkeypatch.setenv("DEMO_REF_USER", "ref_admin")
    monkeypatch.setenv("DEMO_REF_PASSWORD", "ref-pw")
    registry = DatasourceRegistry(SecretManager(), adapter_registry({"postgres": PostgresAdapter}))
    config = Datasource(
        id="analytics_db",
        type="postgres", connection=dict(host="${env:DEMO_REF_HOST}",
            port="${env:DEMO_REF_PORT}",
            user="${env:DEMO_REF_USER}",
            password="${env:DEMO_REF_PASSWORD}",
            database="analytics_db",
        ),
    )

    # Act
    adapter = registry.register_datasource(config)

    # Assert
    assert adapter.url(adapter.connection).render_as_string(hide_password=False) == (
        "postgresql+psycopg2://ref_admin:ref-pw@analytics_db:5432/analytics_db?sslmode=prefer"
    )


def test_registration_errors_do_not_echo_the_resolved_password(monkeypatch):
    # A pydantic ValidationError renders the whole input dict, and `nl2sql doctor`
    # prints that message verbatim. The plaintext must not survive the trip.
    # Arrange
    monkeypatch.setenv("DEMO_REF_PASSWORD", "s3cret-pw")
    registry = DatasourceRegistry(SecretManager(), adapter_registry({"postgres": PostgresAdapter}))
    config = Datasource(
        id="analytics_db",
        type="postgres", connection=dict(host="analytics_db",
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
