"""One broken datasource no longer stops the engine from starting.

The others register; the broken one is recorded as unavailable with its
reason, and anything that targets it (a query, indexing, an adapter lookup)
fails with that reason. If no datasource at all can start, startup still fails.
"""
import logging
from types import SimpleNamespace

import pytest

from nl2sql import NL2SQL
from nl2sql.datasources.models import ConnectionConfig, DatasourceConfig
from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.secrets import SecretManager


class _StubAdapter:
    def __init__(self, datasource_id, datasource_engine_type, connection_args, **kwargs):
        self.datasource_id = datasource_id
        self.datasource_engine_type = datasource_engine_type
        self.connection_args = connection_args

    def capabilities(self):
        return {"supports_sql"}

    def get_dialect(self):
        return "sqlite"


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr("nl2sql.datasources.registry.discover_adapters", lambda: {"stub": _StubAdapter})
    return DatasourceRegistry(SecretManager())


def _good(ds_id="good"):
    return DatasourceConfig(id=ds_id, connection=ConnectionConfig(type="stub"))


def _unknown_type(ds_id="bad"):
    return DatasourceConfig(id=ds_id, connection=ConnectionConfig(type="nosuchdb"))


def test_a_bad_datasource_is_recorded_unavailable_and_the_others_register(registry, caplog):
    with caplog.at_level(logging.ERROR):
        registry.register_datasources([_unknown_type(), _good()])

    assert registry.list_ids() == ["good"]
    unavailable = registry.unavailable()
    assert list(unavailable) == ["bad"]
    assert "nosuchdb" in unavailable["bad"]
    assert any("'bad'" in r.getMessage() and "unavailable" in r.getMessage() for r in caplog.records)


def test_an_unresolved_secret_makes_only_that_datasource_unavailable(registry, monkeypatch):
    monkeypatch.delenv("NL2SQL_TEST_UNSET_PASSWORD", raising=False)
    secret = DatasourceConfig(
        id="needs_secret",
        connection=ConnectionConfig(type="stub", password="${env:NL2SQL_TEST_UNSET_PASSWORD}"),
    )

    registry.register_datasources([secret, _good()])

    assert registry.list_ids() == ["good"]
    assert "NL2SQL_TEST_UNSET_PASSWORD" in registry.unavailable()["needs_secret"]


def test_looking_up_an_unavailable_datasource_says_why(registry):
    registry.register_datasources([_unknown_type(), _good()])

    with pytest.raises(ValueError) as excinfo:
        registry.get_adapter("bad")
    assert "unavailable" in str(excinfo.value)
    assert "nosuchdb" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        registry.get_capabilities("bad")
    assert "nosuchdb" in str(excinfo.value)
    assert registry.supports("bad", "supports_sql") is False


def test_startup_still_fails_when_no_datasource_is_available(registry):
    with pytest.raises(ValueError) as excinfo:
        registry.register_datasources([_unknown_type("bad1"), _unknown_type("bad2")])

    message = str(excinfo.value)
    assert "bad1" in message and "bad2" in message


def test_no_configured_datasources_is_not_a_failure(registry):
    registry.register_datasources([])
    assert registry.list_ids() == []


def test_registering_a_fixed_datasource_clears_its_unavailable_entry(registry):
    registry.register_datasources([_unknown_type("ds"), _good()])
    registry.register_datasource(_good("ds"))
    assert "ds" not in registry.unavailable()


# A query naming an unavailable datasource: see test_node_datasource_resolver.py.


def test_indexing_an_unavailable_datasource_fails_with_the_reason():
    from nl2sql.indexing.rebuild import rebuild_index

    ctx = SimpleNamespace(
        vector_store=None,
        ds_registry=SimpleNamespace(
            list_adapters=lambda: [],
            unavailable=lambda: {"bad": "No adapter found for engine type: 'nosuchdb'"},
        ),
    )

    result = rebuild_index(ctx, datasource_ids=["bad"])

    assert not result.ok
    assert "nosuchdb" in result.errors[0]["error"]


def test_policy_validation_says_a_referenced_datasource_failed_to_start(tmp_path):
    import json

    from nl2sql.api.policy_api import PolicyAPI

    (tmp_path / "datasources.yaml").write_text(
        "version: 1\n"
        "datasources:\n"
        "  - id: good\n"
        "    connection: {type: sqlite, database: \":memory:\"}\n"
        "  - id: bad\n"
        "    connection: {type: nosuchdb}\n",
        encoding="utf-8",
    )
    (tmp_path / "policies.json").write_text(json.dumps({
        "version": 1,
        "roles": {"analyst": {"description": "d", "role": "analyst",
                              "allowed_datasources": ["bad"], "allowed_tables": ["bad.*"]}},
    }), encoding="utf-8")

    report = PolicyAPI().validate_policies(
        policies_path=tmp_path / "policies.json",
        datasources_path=tmp_path / "datasources.yaml",
        secrets_path=tmp_path / "secrets.yaml",
    )

    entry = next(e for e in report.entries if e.target == "Datasource: bad")
    assert entry.status == "UNAVAILABLE"
    assert "nosuchdb" in entry.details
    assert not report.ok


def test_index_health_and_the_facade_report_unavailable_datasources(registry):
    registry.register_datasources([_unknown_type(), _good()])
    engine = NL2SQL.__new__(NL2SQL)
    engine._ctx = SimpleNamespace(vector_store=None, schema_store=None, ds_registry=registry)

    assert "nosuchdb" in engine.list_unavailable_datasources()["bad"]
    health = engine.index_health()
    assert any("'bad'" in w and "nosuchdb" in w for w in health["warnings"])
