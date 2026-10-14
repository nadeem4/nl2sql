"""``check()`` warns about a datasource whose adapter cannot make its session read-only (owner decision 4).

docs/architecture/phase2.md, section 7, "Read-only and timeouts, honestly".
"""
from __future__ import annotations

import sqlite3

from langchain_core.embeddings import DeterministicFakeEmbedding

from nl2sql import NL2SQL, Embeddings, Registry
from nl2sql.testing import OpenConnection
from nl2sql.testing.config import make_config
from nl2sql_sdk import ConnectionCheck, DatasourceCapability


class WritableAdapter:
    """An adapter that connects but cannot stop its session from writing."""

    interface_version = 2
    title = "Writable"
    connection_model = OpenConnection
    driver_modules = ()
    install_hint = ""

    def __init__(self, datasource_id=None, connection=None, limits=None, *, folder=None):
        self.datasource_id = datasource_id

    def capabilities(self):
        return {DatasourceCapability.SUPPORTS_SQL, DatasourceCapability.ENFORCES_TIMEOUT}

    def test_connection(self):
        return ConnectionCheck(ok=True)

    def fetch_schema_snapshot(self, scope, on_table=None):
        raise AssertionError("not reached")

    def execute(self, request):
        raise AssertionError("not reached")

    def get_dialect(self):
        return "sqlite"

    def close(self):
        pass


def test_check_warns_when_read_only_is_not_enforced(tmp_path):
    with sqlite3.connect(tmp_path / "shop.db") as connection:
        connection.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY)")
    registry = Registry.default()
    registry.add_adapter("writable", WritableAdapter)
    config = make_config(tmp_path, datasources=[
        {"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}},
        {"id": "sales", "type": "writable", "connection": {"host": "db.internal"}},
    ]).model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8))})
    engine = NL2SQL(config, registry=registry)

    report = engine.check()

    status = {d.id: d for d in report.datasources}
    # Still ready and routed: the typed plan and the validator block every write.
    assert status["sales"].status == "ready"
    assert status["sales"].warnings == (
        "sales (writable): this adapter cannot make the session read-only. "
        "Connect with a login that can only read.",)
    assert status["shop"].warnings == ()
    assert "sales: ready\n  Warning: sales (writable): this adapter cannot make the session read-only" in str(
        status["sales"])
    engine.close()
