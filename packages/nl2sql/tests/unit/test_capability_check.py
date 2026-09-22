"""One capability check, in the registry, that fails closed (boundary audit F12).

The registry used to grant SUPPORTS_SQL to an adapter without ``capabilities``
while the executor node denied one whose ``capabilities()`` raised. Now
``DatasourceRegistry.supports`` is the only check, and anything it cannot
establish -- an unknown datasource, a missing or failing ``capabilities()`` --
is a no.
"""
from types import SimpleNamespace

import pytest

from nl2sql.datasources.models import ConnectionConfig, DatasourceConfig
from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.execution.contracts import ExecutorRequest
from nl2sql.execution.executor.sql_executor import SqlExecutorService
from nl2sql.pipeline.graph_utils import SQL_AGENT_SUBGRAPH, resolve_subgraph
from nl2sql.secrets.manager import SecretManager
from nl2sql_adapter_sdk.capabilities import DatasourceCapability

SQL = DatasourceCapability.SUPPORTS_SQL


class _Adapter:
    caps = {SQL}

    def __init__(self, datasource_id, datasource_engine_type, connection_args, **kwargs):
        self.datasource_id = datasource_id

    def capabilities(self):
        if isinstance(self.caps, Exception):
            raise self.caps
        return self.caps

    def get_dialect(self):
        return "sqlite"


class _Rest(_Adapter):
    caps = {DatasourceCapability.SUPPORTS_REST}


class _Broken(_Adapter):
    caps = RuntimeError("boom")


class _NoCapabilities:
    def __init__(self, datasource_id, datasource_engine_type, connection_args, **kwargs):
        pass


def registry_with(monkeypatch, **adapters) -> DatasourceRegistry:
    """A registry with one datasource per keyword, named and typed by it."""
    monkeypatch.setattr("nl2sql.datasources.registry.discover_adapters", lambda: dict(adapters))
    registry = DatasourceRegistry(SecretManager())
    for name in adapters:
        registry.register_datasource(DatasourceConfig(id=name, connection=ConnectionConfig(type=name)))
    return registry


def test_supports_reports_a_declared_capability(monkeypatch):
    registry = registry_with(monkeypatch, sql=_Adapter, rest=_Rest)

    assert registry.supports("sql", SQL)
    assert registry.supports("sql", SQL.value)
    assert not registry.supports("rest", SQL)


def test_a_failing_capabilities_call_denies_rather_than_breaking_registration(monkeypatch):
    registry = registry_with(monkeypatch, broken=_Broken)

    assert registry.get_capabilities("broken") == set()
    assert not registry.supports("broken", SQL)


def test_an_adapter_without_capabilities_is_not_granted_sql(monkeypatch):
    registry = registry_with(monkeypatch, bare=_NoCapabilities)

    assert not registry.supports("bare", SQL)


def test_an_unknown_datasource_supports_nothing(monkeypatch):
    registry = registry_with(monkeypatch)

    assert not registry.supports("missing", SQL)


@pytest.mark.parametrize("ds_id, allowed", [("sql", True), ("rest", False), ("broken", False), ("missing", False)])
def test_routing_and_the_sql_executor_ask_the_registry(monkeypatch, ds_id, allowed):
    registry = registry_with(monkeypatch, sql=_Adapter, rest=_Rest, broken=_Broken)

    assert (resolve_subgraph(ds_id, SimpleNamespace(ds_registry=registry)) == SQL_AGENT_SUBGRAPH) is allowed
    request = ExecutorRequest(node_id="n", trace_id="t", subgraph_name="sql_agent",
                              datasource_id=ds_id, sql="SELECT 1", tenant_id="t1")
    errors = SqlExecutorService(registry).validate_request(request)
    assert (not errors) is allowed
