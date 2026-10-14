"""``engine.datasources.types()``, ``test(Datasource)`` and ``add()``: the methods behind Connect a database.

See docs/architecture/phase2.md, section 7, "`engine.datasources`: the methods
behind Connect a database". A real engine on a small SQLite database and a
fake embedder; stub adapters stand in for a driver that is missing and for a
server that refuses a login.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from pydantic import SecretStr

from nl2sql import NL2SQL, Datasource, DatasourceStatus, DatasourceType, Embeddings, Registry
from nl2sql.testing.config import make_config
from nl2sql_sdk import ConnectionCheck, ConnectionModel

SHOP = {"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}}


class GhostConnection(ConnectionModel):
    host: str
    password: SecretStr


class GhostAdapter:
    """An adapter whose driver is not installed."""

    interface_version = 2
    title = "Ghost"
    connection_model = GhostConnection
    driver_modules = ("nl2sql_no_such_driver",)
    install_hint = 'pip install "ghost-driver"'


class RefusingAdapter:
    """An adapter whose server refuses the login; it records being closed."""

    interface_version = 2
    title = "Refusing"
    connection_model = GhostConnection
    driver_modules = ()
    install_hint = ""
    closed = []

    def __init__(self, datasource_id, connection, limits, *, folder=None):
        self.datasource_id = datasource_id

    def capabilities(self):
        return set()

    def test_connection(self):
        return ConnectionCheck(ok=False, stage="auth", message="login refused",
                               fix="check the user and password")

    def close(self):
        RefusingAdapter.closed.append(self.datasource_id)


def _registry() -> Registry:
    registry = Registry.default()
    registry.add_adapter("ghost", GhostAdapter)
    registry.add_adapter("refusing", RefusingAdapter)
    return registry


@pytest.fixture
def engine(tmp_path: Path):
    with sqlite3.connect(tmp_path / "shop.db") as connection:
        connection.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
    cfg = make_config(tmp_path, datasources=[SHOP])
    cfg = cfg.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8))})
    with NL2SQL(cfg, registry=_registry()) as engine:
        yield engine


# ---------------------------------------------------------------- types

def test_types_lists_uninstalled_adapters_with_schema(engine):
    types = {t.name: t for t in engine.datasources.types()}

    assert all(isinstance(t, DatasourceType) for t in types.values())
    assert {"sqlite", "postgres", "ghost", "refusing"} <= set(types)
    ghost = types["ghost"]
    assert ghost.title == "Ghost" and ghost.installed is False
    assert ghost.install_hint == 'pip install "ghost-driver"'
    assert set(ghost.connection_schema["properties"]) == {"host", "password"}
    assert types["sqlite"].installed is True
    assert "database" in types["sqlite"].connection_schema["properties"]


def test_secret_field_is_a_password_in_the_schema(engine):
    types = {t.name: t for t in engine.datasources.types()}

    for name in ("postgres", "ghost"):
        password = types[name].connection_schema["properties"]["password"]
        assert password["format"] == "password" and password["writeOnly"] is True
    assert "format" not in types["postgres"].connection_schema["properties"]["host"]


# ---------------------------------------------------------------- test

def test_test_registers_nothing(engine):
    RefusingAdapter.closed.clear()
    ledger = Datasource(id="ledger", type="sqlite", connection={"database": "shop.db"})
    refused = Datasource(id="vault", type="refusing", connection={"host": "db", "password": "x"})

    ready = engine.datasources.test(ledger)
    engine.datasources.test(refused)

    assert isinstance(ready, DatasourceStatus) and ready.status == "ready" and ready.id == "ledger"
    assert [d.id for d in engine.datasources.list()] == ["shop"]
    assert [d.id for d in engine.config.datasources] == ["shop"]
    with pytest.raises(ValueError):
        engine.datasources.get("ledger")
    assert RefusingAdapter.closed == ["vault"]


def test_test_reports_the_stage(engine):
    refused = engine.datasources.test(Datasource(id="vault", type="refusing",
                                                 connection={"host": "db", "password": "hunter2"}))
    no_driver = engine.datasources.test(Datasource(id="g", type="ghost", connection={"host": "db", "password": "x"}))
    typo = engine.datasources.test(Datasource(id="t", type="refusing",
                                              connection={"hostname": "db", "password": "hunter2"}))
    unknown = engine.datasources.test(Datasource(id="u", type="postgress", connection={}))

    assert (refused.status, refused.stage, refused.reason, refused.fix) == (
        "failed", "auth", "login refused", "check the user and password")
    assert no_driver.stage == "driver" and no_driver.fix == 'pip install "ghost-driver"'
    assert "nl2sql_no_such_driver" in no_driver.reason
    assert typo.stage == "config" and "Did you mean 'host'?" in typo.reason
    assert "hunter2" not in str(typo) and "hunter2" not in str(refused)
    assert unknown.stage == "config" and "Did you mean 'postgres'?" in unknown.reason


def test_test_with_an_id_connects_the_configured_datasource(engine):
    status = engine.datasources.test("shop")

    assert status.status == "ready" and engine.datasources.get("shop").status == "ready"


# ---------------------------------------------------------------- add

def test_add_validates_index_scope(engine):
    valid = Datasource(id="ledger", type="sqlite", connection={"database": "shop.db"})
    # model_copy(update=...) skips validation, so add() checks the scope itself.
    bad = valid.model_copy(update={"id": "bad", "index": {"include_tables": ["orders"]}})
    raw = valid.model_copy(update={"index": {"exclude_schemas": ["audit"], "profile": "catalog"}})

    with pytest.raises(ValueError, match=r"index\.include_tables: .*write '\*\.orders'"):
        engine.datasources.add(bad)
    assert [d.id for d in engine.config.datasources] == ["shop"]

    engine.datasources.add(raw)
    added = next(d for d in engine.config.datasources if d.id == "ledger")
    assert added.index.exclude_schemas == ["audit"] and added.index.profile == "catalog"


def test_add_validates_the_connection_against_the_model(engine):
    with pytest.raises(ValueError, match="Did you mean 'host'"):
        engine.datasources.add(Datasource(id="vault", type="refusing",
                                          connection={"hostname": "db", "password": "x"}))
    assert [d.id for d in engine.config.datasources] == ["shop"]
