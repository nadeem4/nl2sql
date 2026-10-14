"""The adapter SDK v2 contract: strict connection models, ``IndexScope``, version 2.

docs/architecture/phase2.md, section 7.
"""
import pytest
from pydantic import SecretStr, ValidationError

from nl2sql.adapters.postgres.adapter import PostgresAdapter, PostgresConnection
from nl2sql.adapters.sqlite.adapter import SqliteAdapter, SqliteConnection
from nl2sql.config import Datasource
from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.registry import Registry, RegistryError
from nl2sql.secrets.manager import SecretManager
from nl2sql.testing import adapter_registry
from nl2sql_sdk import (ConnectionCheck, ConnectionModel, DatasourceAdapter, DatasourceCapability, IndexScope,
                        Limits, TableRef)

BUILT_INS = ("sqlite", "duckdb", "postgres", "mysql", "mssql")


def _registry() -> DatasourceRegistry:
    return DatasourceRegistry(SecretManager(), Registry.default())


# ------------------------------------------------------------- connection models

def test_unknown_connection_key_suggests_the_field():
    datasource = Datasource(id="sales", type="postgres",
                            connection={"hostname": "db", "database": "d", "user": "u", "password": "p"})

    with pytest.raises(ValueError) as error:
        _registry().register_datasource(datasource)

    message = str(error.value)
    assert "hostname" in message
    assert "Did you mean 'host'?" in message
    assert "'p'" not in message


def test_connection_model_instance_is_accepted(tmp_path):
    connection = SqliteConnection(database=str(tmp_path / "shop.sqlite"))

    adapter = _registry().register_datasource(Datasource(id="shop", type="sqlite", connection=connection))

    assert adapter.connection is connection


def test_a_dict_connection_is_validated_into_the_model(monkeypatch):
    monkeypatch.setenv("SALES_PASSWORD", "s3cret")
    datasource = Datasource(id="sales", type="postgres", connection={
        "host": "db", "database": "d", "user": "u", "password": "${env:SALES_PASSWORD}"})

    adapter = _registry().register_datasource(datasource)

    assert isinstance(adapter.connection, PostgresConnection)
    assert adapter.connection.password.get_secret_value() == "s3cret"
    assert "s3cret" not in repr(adapter.connection)


def test_another_adapters_model_is_refused():
    datasource = Datasource(id="sales", type="sqlite",
                            connection=PostgresConnection(host="db", database="d", user="u", password="p"))

    with pytest.raises(ValueError, match="SqliteConnection"):
        _registry().register_datasource(datasource)


def test_a_bad_value_never_echoes_the_secret():
    datasource = Datasource(id="sales", type="postgres", connection={
        "host": "db", "database": "d", "user": "u", "password": "hunter2", "port": "not-a-port"})

    with pytest.raises(ValueError) as error:
        _registry().register_datasource(datasource)

    assert "port" in str(error.value)
    assert "hunter2" not in str(error.value)


@pytest.mark.parametrize("name", BUILT_INS)
def test_every_built_in_declares_a_strict_public_model(name):
    adapter_cls = _load_without_driver(name)

    model = adapter_cls.connection_model
    assert issubclass(model, ConnectionModel)
    assert model.model_config["extra"] == "forbid"
    assert adapter_cls.title
    with pytest.raises(ValidationError):
        model.model_validate({"database": "d", "host": "h", "user": "u", "password": "p", "typo": 1})


def test_a_secret_field_is_a_password_in_the_schema():
    schema = PostgresConnection.model_json_schema()

    assert schema["properties"]["password"]["format"] == "password"
    assert schema["properties"]["password"]["writeOnly"] is True


def _load_without_driver(name):
    from importlib.metadata import entry_points

    (entry,) = [e for e in entry_points(group="nl2sql.adapters") if e.name == name]
    return entry.load()


# ------------------------------------------------------------- version 2

def test_adapter_version_1_is_rejected():
    class OldAdapter:
        interface_version = 1
        driver_modules = ()
        install_hint = ""

    registry = adapter_registry({"old": OldAdapter})

    with pytest.raises(RegistryError, match="interface 1; this engine needs 2"):
        registry.load("adapter", "old")


@pytest.mark.parametrize("name", BUILT_INS)
def test_every_built_in_is_version_2(name):
    assert _load_without_driver(name).interface_version == 2


def test_the_built_in_satisfies_the_protocol(tmp_path):
    adapter = SqliteAdapter("shop", SqliteConnection(database=str(tmp_path / "x.sqlite")), Limits())

    assert isinstance(adapter, DatasourceAdapter)


def test_building_an_adapter_never_connects(tmp_path):
    adapter = SqliteAdapter("shop", SqliteConnection(database=str(tmp_path / "missing.sqlite")), Limits())

    assert not (tmp_path / "missing.sqlite").exists()
    adapter.close()


def test_test_connection_returns_a_connection_check(tmp_path):
    adapter = SqliteAdapter("shop", SqliteConnection(database=str(tmp_path / "missing.sqlite")), Limits())

    check = adapter.test_connection()

    assert isinstance(check, ConnectionCheck)
    assert check.ok is False
    assert check.message


def test_the_rest_capability_is_gone_and_enforcement_is_declared():
    names = {c.name for c in DatasourceCapability}

    assert "SUPPORTS_REST" not in names
    assert {"ENFORCES_READ_ONLY", "ENFORCES_TIMEOUT"} <= names


def test_limits_live_in_the_sdk():
    import nl2sql.config as config

    assert config.Limits is Limits


# ------------------------------------------------------------- IndexScope

def _ref(schema, table):
    return TableRef(schema_name=schema, table_name=table)


def test_index_scope_globs():
    scope = IndexScope(include_schemas=["public", "report*"], exclude_schemas=["reporting_old"],
                       exclude_tables=["*.*_audit", "public.tmp_*"],
                       exclude_columns=["public.customers.email"])

    assert scope.wants_table(_ref("public", "orders"))
    assert scope.wants_table(_ref("reporting", "daily"))
    assert not scope.wants_table(_ref("reporting_old", "daily"))
    assert not scope.wants_table(_ref("sales", "orders"))
    assert not scope.wants_table(_ref("public", "orders_audit"))
    assert not scope.wants_table(_ref("public", "tmp_load"))
    assert not scope.wants_column(_ref("public", "customers"), "email")
    assert scope.wants_column(_ref("public", "customers"), "name")


def test_index_scope_include_tables_and_case():
    scope = IndexScope(include_tables=["Sales.Order*"])

    assert scope.wants_table(_ref("sales", "orders"))
    assert scope.wants_table(_ref("SALES", "OrderLines"))
    assert not scope.wants_table(_ref("sales", "customers"))


def test_an_empty_index_scope_wants_everything():
    scope = IndexScope()

    assert scope.wants_table(_ref("any", "table"))
    assert scope.wants_column(_ref("any", "table"), "column")
    assert scope.profile == "stats"
    assert scope.sample_values is False


def test_index_scope_keys_are_strict():
    with pytest.raises(ValidationError):
        IndexScope(include_schema=["public"])


def test_datasource_index_is_an_index_scope():
    datasource = Datasource(id="sales", type="postgres", index={"exclude_tables": ["*.tmp_*"]})

    assert isinstance(datasource.index, IndexScope)
    assert not datasource.index.wants_table(_ref("public", "tmp_x"))
    with pytest.raises(ValidationError):
        Datasource(id="sales", type="postgres", index={"sample_value": True})


def test_the_snapshot_holds_only_what_the_scope_wants(tmp_path):
    import sqlite3

    path = tmp_path / "shop.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
        con.execute("CREATE TABLE orders_audit (id INTEGER PRIMARY KEY)")
    adapter = SqliteAdapter("shop", SqliteConnection(database=str(path)), Limits())
    seen = []

    snapshot = adapter.fetch_schema_snapshot(
        IndexScope(exclude_tables=["*.*_audit"], exclude_columns=["main.customers.email"]), on_table=seen.append)
    adapter.close()

    assert [t.table.table_name for t in snapshot.contract.tables.values()] == ["customers"]
    [contract] = snapshot.contract.tables.values()
    [metadata] = snapshot.metadata.tables.values()
    assert sorted(contract.columns) == sorted(metadata.columns) == ["id", "name"]
    assert seen == ["main.customers"]


def test_adapter_reads_the_datasource_limits(tmp_path):
    limits = Limits(row_limit=5, timeout_ms=1000)
    adapter = _registry().register_datasource(
        Datasource(id="shop", type="sqlite", connection={"database": str(tmp_path / "x.sqlite")}, limits=limits))

    assert adapter.limits == limits
