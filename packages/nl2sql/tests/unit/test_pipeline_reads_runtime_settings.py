"""The pipeline reads its settings from the runtime it is given, not from a process global.

Two runtimes with different settings in one process each get their own values:
the nodes read ``ctx.settings`` (the ``settings`` section of the engine's
``Config``), the executor and the aggregator write to ``ctx.artifact_store``,
and a module with no runtime takes the value as a parameter (``run_date(today)``).
See docs/architecture/configuration_v2.md, section 8.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from nl2sql.common.dates import run_date
from nl2sql.config import Policies, Settings
from nl2sql.execution.artifacts import ArtifactStore, ArtifactStoreConfig
from nl2sql.execution.contracts import ExecutorRequest
from nl2sql.execution.executor.sql_executor import SqlExecutorService
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, Expr, PlanModel, SelectItem, TableRef
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.generator.node import GeneratorNode
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.plan_cache import PlanCache
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.schema import InMemorySchemaStore
from nl2sql_sdk.contracts import ResultFrame


def _settings(**sections) -> Settings:
    return Settings.model_validate(sections)


def test_run_date_is_the_pinned_date():
    assert run_date(dt.date(2013, 12, 31)) == dt.date(2013, 12, 31)


def test_run_date_without_a_pin_is_today_in_utc():
    assert run_date(None) == dt.datetime.now(dt.timezone.utc).date()


def _generate(settings: Settings):
    adapter = SimpleNamespace(row_limit=None, max_bytes=1000, get_dialect=lambda: "sqlite")
    ctx = SimpleNamespace(ds_registry=SimpleNamespace(get_adapter=lambda _id: adapter), settings=settings)
    plan = PlanModel(tables=[TableRef(name="users", alias="u")],
                     select_items=[SelectItem(expr=Expr(kind="column", alias="u", column_name="id"))], joins=[])
    state = SubgraphExecutionState(trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="ds", intent="q"),
                                   ast_planner_response=ASTPlannerResponse(plan=plan))
    return GeneratorNode(ctx)(state)["generator_response"]


def test_two_runtimes_in_one_process_cap_rows_at_their_own_ceiling():
    small = _generate(_settings(results={"row_limit": 5}))
    large = _generate(_settings(results={"row_limit": 50}))

    assert small.row_cap == 5 and "LIMIT 5" in small.sql_draft
    assert large.row_cap == 50 and "LIMIT 50" in large.sql_draft


def test_the_validator_takes_strict_columns_and_the_refusal_policy_from_its_runtime():
    def node(strict, names_tables):
        ctx = SimpleNamespace(ds_registry=SimpleNamespace(), rbac=SimpleNamespace(),
                              settings=_settings(pipeline={"strict_columns": strict}),
                              config=SimpleNamespace(policies=Policies(refusal_names_tables=names_tables)))
        return LogicalValidatorNode(ctx)

    assert node(True, False).strict_columns is True
    assert node(False, True).strict_columns is False
    assert node(False, True).refusal_names_tables is True
    assert node(True, False).refusal_names_tables is False


def _sub_query():
    return SubQuery(id="sq1", datasource_id="ds", intent="How many customers?", schema_version="v1")


def _plan():
    return PlanModel(tables=[TableRef(name="Customer", alias="t1")],
                     select_items=[SelectItem(expr=Expr(kind="column", alias="t1", column_name="Id"))])


def test_a_plan_cache_turned_off_in_the_settings_neither_reads_nor_writes():
    store = InMemorySchemaStore()

    assert PlanCache(store, enabled=False).put(_sub_query(), _plan()) is False
    assert PlanCache(store, enabled=True).put(_sub_query(), _plan()) is True
    assert PlanCache(store, enabled=False).get(_sub_query()) is None


def test_the_plan_cache_keys_on_the_date_it_is_given():
    store = InMemorySchemaStore()
    PlanCache(store, today=dt.date(2013, 12, 31)).put(_sub_query(), _plan())

    assert PlanCache(store, today=dt.date(2013, 12, 31)).get(_sub_query()) == _plan()
    assert PlanCache(store, today=dt.date(2014, 1, 1)).get(_sub_query()) is None


class _StreamingAdapter:
    def __init__(self):
        self.batch_sizes = []

    def execute(self, request):
        raise AssertionError("read through execute_batches")

    def execute_batches(self, request, batch_size):
        self.batch_sizes.append(batch_size)
        yield ResultFrame(columns=["id"], rows=[[1]], row_count=1)


def test_the_executor_writes_to_the_store_and_batch_size_it_is_given(tmp_path):
    adapter = _StreamingAdapter()
    registry = SimpleNamespace(get_adapter=lambda ds: adapter, supports=lambda ds, cap: True)
    store = ArtifactStore(ArtifactStoreConfig(base_uri=str(tmp_path / "results")))
    service = SqlExecutorService(registry, store, fetch_batch_size=7)

    response = service.execute(ExecutorRequest(node_id="sq1", trace_id="r1", subgraph_name="sql_agent",
                                               datasource_id="ds", sql="SELECT id FROM t", tenant_id="t1"))

    assert response.errors == []
    assert adapter.batch_sizes == [7]
    assert response.artifact.uri.startswith(str((tmp_path / "results").resolve()))
