"""A datasource's ``limits`` reach its adapter, and its row limit caps the generated SQL."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nl2sql.common.settings import settings
from nl2sql.config import Datasource, Limits
from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, Expr, PlanModel, SelectItem, TableRef
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.generator.node import GeneratorNode
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.secrets.manager import SecretManager
from nl2sql.testing import adapter_registry


class _StubAdapter:
    interface_version = 1
    driver_modules = ()
    install_hint = ""

    def __init__(self, datasource_id, datasource_engine_type, connection_args, **kwargs):
        self.kwargs = kwargs
        self.row_limit = kwargs.get("row_limit")
        self.max_bytes = kwargs.get("max_bytes")

    def capabilities(self):
        return set()

    def get_dialect(self):
        return "sqlite"


@pytest.fixture
def registry(monkeypatch):
    return DatasourceRegistry(SecretManager(), adapter_registry({"stub": _StubAdapter}))


def _sql_for(registry, ds_yaml: dict) -> str:
    registry.register_datasource(Datasource(**ds_yaml))
    node = GeneratorNode(SimpleNamespace(ds_registry=registry))
    plan = PlanModel(tables=[TableRef(name="users", alias="u")],
                     select_items=[SelectItem(expr=Expr(kind="column", alias="u", column_name="id"))],
                     joins=[])
    state = SubgraphExecutionState(trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
                                   ast_planner_response=ASTPlannerResponse(plan=plan))
    return node(state)["generator_response"].sql_draft


def test_a_datasource_row_limit_caps_the_generated_limit(registry, monkeypatch):
    monkeypatch.setattr(settings, "result_row_limit", 100_000)
    sql = _sql_for(registry, {"id": "ds1", "type": "stub", "limits": Limits(row_limit=100)})

    assert "LIMIT 100" in sql and "LIMIT 100000" not in sql


def test_the_datasource_timeout_and_max_bytes_reach_the_adapter(registry):
    adapter = registry.register_datasource(Datasource(
        id="ds1", type="stub", limits=Limits(timeout_ms=8000, max_bytes=1024),
    ))

    assert adapter.kwargs["statement_timeout_ms"] == 8000
    assert adapter.kwargs["max_bytes"] == 1024

