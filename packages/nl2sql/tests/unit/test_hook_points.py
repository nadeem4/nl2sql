"""The six hook points where the pipeline calls them (docs/architecture/wave_a.md, section 6).

``run.before`` in the run's worker, ``datasources.after`` in the resolver,
``plan.after`` in the planner (on a cached plan too), ``execute.before`` and
``execute.after`` in the executor and the curated matcher, ``answer.after``
in the answer synthesizer. A deny scoped to a sub-query drops that sub-query
(and a join or compare group it feeds) and lets the rest of the run go on.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END

from test_curated_matcher import CHOOSE_TOP, COUNT, QUESTION, TOP, _Registry, _ctx, _server, db  # noqa: F401
from test_plan_cache import _artifact, _executed, _plan, _sq, _wrapper_state, store  # noqa: F401

from nl2sql.common.errors import ErrorCode, ErrorSeverity
from nl2sql.hooks import HOOK_POINTS, Allow, Deny, HookBinding, HookRunner, Modify
from nl2sql.pipeline.graph_utils import wrap_subgraph
from nl2sql.pipeline.nodes.ast_planner.node import ASTPlannerNode
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, PlanModel
from nl2sql.pipeline.nodes.decomposer.dag import build_execution_dag
from nl2sql.pipeline.nodes.decomposer.schemas import CombineGroup, CombineInput, DecomposerResponse, SubQuery
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.pipeline.plan_cache import PlanCache
from nl2sql.pipeline.state import GraphState, SubgraphExecutionState
from nl2sql.pipeline.subgraphs.schemas import SubgraphOutput
from nl2sql.policies import UserContext
from nl2sql.testing.runtime import fake_runtime

ALL = frozenset(HOOK_POINTS)


def _hooks(*decide, points=ALL):
    """A runner over functions of ``ctx``; each also records ``(point, ctx)`` in ``.seen``."""
    seen = []

    def recorder(fn):
        def hook(ctx):
            seen.append((ctx.point, ctx))
            return fn(ctx)
        return hook

    runner = HookRunner([HookBinding(name=f"h{i}", hook=recorder(fn), points=frozenset(points), on_error="closed",
                                     approver_roles=None) for i, fn in enumerate(decide)])
    runner.seen = seen
    return runner


def _points(runner):
    return [point for point, _ in runner.seen]


# ---------------------------------------------------------------- run.before


def _run(monkeypatch, hooks, question="How many customers?"):
    from nl2sql.pipeline import runtime

    invoked = []

    class Graph:
        def invoke(self, state, config=None):
            invoked.append(state)
            return state

    monkeypatch.setattr(runtime, "build_graph", lambda ctx, execute=True: Graph())
    ctx = fake_runtime(hooks=hooks)
    out = runtime.run_with_graph(ctx, question, user_context=UserContext(user_id="ada"), trace_mode="off")
    return out, invoked


def test_run_before_may_rewrite_the_question(monkeypatch):
    hooks = _hooks(lambda ctx: Modify(question=ctx.question + " in 2012"))

    out, invoked = _run(monkeypatch, hooks)

    assert invoked[0]["user_query"] == "How many customers? in 2012"
    assert _points(hooks) == ["run.before"]
    [(_, ctx)] = hooks.seen
    assert ctx.user.user_id == "ada" and ctx.execute is True


def test_run_before_deny_ends_the_run_before_the_graph(monkeypatch):
    out, invoked = _run(monkeypatch, _hooks(lambda ctx: Deny(reason="Not today")))

    assert invoked == []
    [error] = out["errors"]
    assert error.error_code == ErrorCode.HOOK_DENIED and error.message == "Not today"


# ---------------------------------------------------------------- plan.after


def _planner(store, hooks, plan=None):
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = fake_runtime(llm_registry=MagicMock(), state=store, hooks=hooks)
    ctx.llm_registry.get_llm.return_value = llm
    ctx.llm_registry.effective.return_value = None
    node = ASTPlannerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.return_value = plan or _plan("from the model")
    return node


def test_plan_after_sees_the_planners_plan(store):
    hooks = _hooks(lambda ctx: Allow())
    node = _planner(store, hooks)

    out = node(SubgraphExecutionState(trace_id="t", user_query="How many?", sub_query=_sq()))

    [(point, ctx)] = hooks.seen
    assert point == "plan.after"
    assert ctx.plan.reasoning == "from the model" and ctx.sub_query.id == "sq1"
    assert ctx.question == "How many?" and ctx.datasource_id == "chinook"
    assert out["ast_planner_response"].plan.reasoning == "from the model"


def test_cached_plan_passes_plan_after(store):
    PlanCache(store).put(_sq(), _plan("cached"))
    hooks = _hooks(lambda ctx: Deny(reason="No cached plans today"))
    node = _planner(store, hooks)

    out = node(SubgraphExecutionState(trace_id="t", sub_query=_sq()))

    node.chain.invoke.assert_not_called()
    assert [ctx.plan.reasoning for _, ctx in hooks.seen] == ["cached"]
    assert out["ast_planner_response"].plan is None
    assert out["errors"][0].error_code == ErrorCode.HOOK_DENIED


def test_cached_plan_is_the_planners_not_the_hooks(store):
    hooked = _plan("changed by a hook")
    node = _planner(store, _hooks(lambda ctx: Modify(plan=hooked)))

    response = node(SubgraphExecutionState(trace_id="t", sub_query=_sq()))["ast_planner_response"]

    assert response.plan == hooked
    assert response.planner_plan.reasoning == "from the model"

    # The wrapper caches the planner's plan, never the hook's.
    ctx = fake_runtime(state=store)
    wrap_subgraph(SimpleNamespace(invoke=lambda state, config=None: {**state, **{
        "ast_planner_response": response,
        "generator_response": GeneratorResponse(sql_draft="SELECT 1"),
        "executor_response": _executed(_artifact()),
    }}), "sql_agent", ctx)(_wrapper_state())
    assert PlanCache(store).get(_sq()).reasoning == "from the model"


def test_a_supplied_plan_passes_plan_after(store):
    # How a later caller (an approved or chosen plan) runs plan.after on a plan it has.
    hooks = _hooks(lambda ctx: Allow())
    node = _planner(store, hooks)

    out = node.after_plan(SubgraphExecutionState(trace_id="t", sub_query=_sq()), _plan("supplied"), "cache")

    assert [ctx.plan.reasoning for _, ctx in hooks.seen] == ["supplied"]
    assert out["ast_planner_response"].plan.reasoning == "supplied"
    node.chain.invoke.assert_not_called()


def test_modified_plan_is_validated(monkeypatch):
    from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
    from nl2sql.pipeline.subgraphs.sql_agent import build_sql_agent_graph

    allowed = PlanModel.model_validate({"tables": [{"name": "orders", "alias": "o"}],
                                        "select_items": [{"expr": {"kind": "column", "alias": "o",
                                                                   "column_name": "id"}}]})
    secret = PlanModel.model_validate({"tables": [{"name": "salaries", "alias": "s"}],
                                       "select_items": [{"expr": {"kind": "column", "alias": "s",
                                                                  "column_name": "id"}}]})
    generated = []
    tables = [Table(name="orders", columns=[Column(name="id", type="int")]),
              Table(name="salaries", columns=[Column(name="id", type="int")])]
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.SchemaRetrieverNode",
                        lambda _ctx: (lambda s: {"relevant_tables": tables}))
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.GeneratorNode",
                        lambda _ctx: (lambda s: generated.append(s) or {}))
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.RefinerNode", lambda _ctx: (lambda s: {}))
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = fake_runtime(llm_registry=MagicMock(), ds_registry=SimpleNamespace(),
                       hooks=_hooks(lambda c: Modify(plan=secret)),
                       policies={"roles": {"user": {"description": "d", "allow": ["ds1.main.orders"]}}},
                       settings={"pipeline": {"retries": {"max": 0}}})
    ctx.llm_registry.get_llm.return_value = llm
    ctx.llm_registry.effective.return_value = None
    monkeypatch.setattr("nl2sql.pipeline.nodes.ast_planner.node.structured",
                        lambda _llm, _model: RunnableLambda(lambda _inputs: allowed))

    result = build_sql_agent_graph(ctx, execute=False).invoke(SubgraphExecutionState(
        trace_id="t", user_query="q", sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        user_context=UserContext(roles=["user"])))

    assert generated == []
    assert any(e.error_code == ErrorCode.SECURITY_VIOLATION for e in result["errors"])


# ---------------------------------------------------------------- datasources.after


def _resolver(hooks):
    """The resolver with its routing stubbed: it chose chinook and support."""
    from nl2sql.pipeline.nodes.datasource_resolver.node import DatasourceResolverNode
    from nl2sql.pipeline.nodes.datasource_resolver.schemas import DatasourceResolverResponse, ResolvedDatasource

    ctx = fake_runtime(hooks=hooks, llm_registry=MagicMock(), vector_store=None,
                       policies={"roles": {"r": {"description": "d", "allow": ["chinook", "support"]}}},
                       ds_registry=SimpleNamespace(list_ids=lambda: ["chinook", "support"]))
    node = DatasourceResolverNode(ctx)
    chosen = DatasourceResolverResponse(
        resolved_datasources=[ResolvedDatasource(datasource_id="chinook"), ResolvedDatasource(datasource_id="support")],
        allowed_datasource_ids=["chinook", "support"])
    node._resolve = lambda state, searches: {"datasource_resolver_response": chosen}
    return node


def test_datasources_after_may_narrow_the_datasources():
    hooks = _hooks(lambda ctx: Modify(datasource_ids=["support"]))

    out = _resolver(hooks)(GraphState(user_query="q", user_context=UserContext(roles=["r"])))

    response = out["datasource_resolver_response"]
    assert [d.datasource_id for d in response.resolved_datasources] == ["support"]
    assert response.allowed_datasource_ids == ["support"]
    [(point, ctx)] = hooks.seen
    assert point == "datasources.after" and ctx.datasource_ids == ["chinook", "support"]


def test_datasources_after_cannot_add_a_datasource():
    out = _resolver(_hooks(lambda ctx: Modify(datasource_ids=["chinook", "payroll"])))(
        GraphState(user_query="q", user_context=UserContext(roles=["r"])))

    assert out["errors"][0].error_code == ErrorCode.HOOK_FAILED
    assert out["datasource_resolver_response"].allowed_datasource_ids == []


def test_datasources_after_deny_ends_the_run():
    from nl2sql.pipeline.routes import resolver_route

    out = _resolver(_hooks(lambda ctx: Deny(reason="No cross-database questions")))(
        GraphState(user_query="q", user_context=UserContext(roles=["r"])))

    assert out["errors"][0].error_code == ErrorCode.HOOK_DENIED
    assert resolver_route(GraphState(user_query="q", **{k: v for k, v in out.items()
                                                         if k == "datasource_resolver_response"})) == "end"


def test_datasources_after_re_applies_the_scope_check():
    hooks = _hooks(lambda ctx: Modify(datasource_ids=["chinook"]))
    node = _resolver(hooks)
    node.policy_engine = SimpleNamespace(scope=lambda user, ds: SimpleNamespace(
        reaches_datasource=lambda: ds != "chinook"))

    out = node(GraphState(user_query="q", user_context=UserContext(roles=["r"])))

    assert out["datasource_resolver_response"].allowed_datasource_ids == []
    assert out["errors"][0].error_code == ErrorCode.SECURITY_VIOLATION


# ---------------------------------------------------------------- execute.before / execute.after


def _executor(db, hooks):
    from nl2sql.adapters.sqlite.adapter import SqliteAdapter, SqliteConnection
    from nl2sql.pipeline.nodes.executor.node import ExecutorNode
    from nl2sql_sdk import Limits

    adapter = SqliteAdapter("shop", SqliteConnection(database=str(db)), Limits())
    ctx = fake_runtime(hooks=hooks, settings={"tenant_id": "t1"}, results=db.parent / "artifacts",
                       ds_registry=_Registry(adapter), policies={"allow_all": True})
    return ExecutorNode(ctx)


def _sub_state(sql="SELECT CustomerId FROM Customer"):
    plan = PlanModel.model_validate({"tables": [{"name": "Customer", "alias": "c"}],
                                     "select_items": [{"expr": {"kind": "column", "alias": "c",
                                                                "column_name": "CustomerId"}}]})
    return SubgraphExecutionState(
        trace_id="t", subgraph_name="sql_agent", user_query="Customers?",
        sub_query=SubQuery(id="sq1", datasource_id="shop", intent="customers", schema_version="v1"),
        ast_planner_response=ASTPlannerResponse(plan=plan),
        generator_response=GeneratorResponse(sql_draft=sql, row_cap=100))


def test_execute_before_sees_the_sql_and_its_tables_and_execute_after_the_summary(db):
    hooks = _hooks(lambda ctx: Allow())

    out = _executor(db, hooks)(_sub_state())

    assert out["executor_response"].artifact.row_count == 3
    assert _points(hooks) == ["execute.before", "execute.after"]
    before, after = (ctx for _, ctx in hooks.seen)
    assert before.sql == "SELECT CustomerId FROM Customer"
    assert before.tables == ["shop.main.Customer"] and before.datasource_id == "shop"
    assert after.result.row_count == 3 and after.result.columns == ["CustomerId"]
    assert not hasattr(after.result, "rows")


def test_execute_before_deny_runs_nothing(db):
    hooks = _hooks(lambda ctx: Deny(reason="No customer data") if ctx.point == "execute.before" else Allow())

    out = _executor(db, hooks)(_sub_state())

    assert out["executor_response"] is None
    assert out["errors"][0].error_code == ErrorCode.HOOK_DENIED
    assert _points(hooks) == ["execute.before"]


def test_execute_after_deny_withholds_the_result(db):
    hooks = _hooks(lambda ctx: Deny(reason="Too many rows") if ctx.point == "execute.after" else Allow())

    out = _executor(db, hooks)(_sub_state())

    assert out["executor_response"] is None
    assert out["errors"][0].error_code == ErrorCode.HOOK_DENIED


def test_plan_only_run_skips_execute_points(monkeypatch):
    from nl2sql.pipeline.subgraphs.sql_agent import build_sql_agent_graph

    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.SchemaRetrieverNode",
                        lambda _ctx: (lambda s: {"relevant_tables": []}))
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.ASTPlannerNode",
                        lambda _ctx: (lambda s: {"ast_planner_response": ASTPlannerResponse(plan=_plan())}))
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.LogicalValidatorNode",
                        lambda _ctx: (lambda s: {}))
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.GeneratorNode",
                        lambda _ctx: (lambda s: {"generator_response": GeneratorResponse(sql_draft="SELECT 1")}))
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.RefinerNode", lambda _ctx: (lambda s: {}))
    hooks = _hooks(lambda ctx: Allow())

    result = build_sql_agent_graph(fake_runtime(hooks=hooks), execute=False).invoke(
        SubgraphExecutionState(trace_id="t", sub_query=_sq()))

    assert result["generator_response"].sql_draft == "SELECT 1"
    assert not [p for p in _points(hooks) if p.startswith("execute.")]


# ---------------------------------------------------------------- the verified path


def _verified(db, hooks):
    from nl2sql.pipeline.nodes.curated_matcher import CuratedMatcherNode

    server = _server(CHOOSE_TOP)
    try:
        ctx = _ctx(db, server, [TOP, COUNT])
        ctx.hooks = hooks
        node = CuratedMatcherNode(ctx)
        return node(GraphState(user_query=QUESTION, user_context=UserContext(roles=["analyst"])))
    finally:
        server.stop()


def test_verified_query_passes_execute_before(db):
    hooks = _hooks(lambda ctx: Allow())

    out = _verified(db, hooks)

    assert out["verified_match"].query_id == "top_customers_by_revenue"
    assert _points(hooks) == ["execute.before", "execute.after"]
    before = hooks.seen[0][1]
    assert before.params == {"n": 2, "year": 2012}
    assert sorted(before.tables) == ["shop.main.Customer", "shop.main.Invoice", "shop.main.InvoiceLine"]
    assert "SUM(il.UnitPrice * il.Quantity)" in before.sql
    assert hooks.seen[1][1].result.row_count == 2


def test_deny_on_verified_path_does_not_fall_back(db):
    from nl2sql.pipeline.graph_utils import completed_scan_ids, next_scan_layer_ids
    from nl2sql.pipeline.routes import build_scan_layer_router, curated_route

    out = _verified(db, _hooks(lambda ctx: Deny(reason="Revenue needs a manager")
                               if ctx.point == "execute.before" else Allow()))

    assert out["errors"][0].error_code == ErrorCode.HOOK_DENIED
    assert not out.get("artifact_refs")
    state = GraphState(user_query=QUESTION, **{k: v for k, v in out.items() if k in GraphState.model_fields})
    # Not the generated path: the run ends.
    assert curated_route(state) == "verified"
    assert build_scan_layer_router(fake_runtime())(state) == END


# ---------------------------------------------------------------- deny scopes


def _output(sq_id, status="success", artifact=None, errors=()):
    return SubgraphOutput(sub_query=SubQuery(id=sq_id, datasource_id="shop", intent=sq_id),
                          subgraph_id=f"sql_agent:{sq_id}:t", status=status, artifact=artifact,
                          errors=list(errors))


def test_sub_query_deny_drops_only_that_sub_query(store):
    verdict = _hooks(lambda ctx: Deny(reason="No payroll", scope="sub_query")).call(
        "execute.before", trace_id="t1", question="q", user=UserContext(), sub_query=_sq(), sql="SELECT 1")
    error = verdict.error("executor")

    out = wrap_subgraph(SimpleNamespace(invoke=lambda state, config=None: {**state, **{
        "ast_planner_response": ASTPlannerResponse(plan=_plan()),
        "generator_response": GeneratorResponse(sql_draft="SELECT 1"),
        "executor_response": None,
        "errors": [error],
    }}), "sql_agent", fake_runtime(state=store))(_wrapper_state())

    assert out["subgraph_outputs"]["sql_agent:sq1:t1"].status == "dropped"
    assert not out.get("errors")
    [warning] = out["warnings"]
    assert warning["error_code"] == "HOOK_DENIED" and warning["severity"] == "WARNING"
    assert "No payroll" in warning["message"]


def test_a_run_scoped_deny_ends_the_run_after_its_layer():
    from nl2sql.pipeline.routes import build_scan_layer_router

    decomposition = DecomposerResponse(
        sub_queries=[SubQuery(id="a", datasource_id="shop", intent="a"),
                     SubQuery(id="b", datasource_id="shop", intent="b")],
        combine_groups=[CombineGroup(group_id="ga", operation="standalone", inputs=[CombineInput(subquery_id="a")]),
                        CombineGroup(group_id="gb", operation="standalone", inputs=[CombineInput(subquery_id="b")])])
    verdict = _hooks(lambda ctx: Deny(reason="No")).call("execute.before", trace_id="t", question="q",
                                                           user=UserContext(), sql="SELECT 1")
    state = GraphState(user_query="q", decomposer_response=decomposition,
                       execution_dag=build_execution_dag(decomposition),
                       artifact_refs={"a": _artifact()},
                       subgraph_outputs={"sql_agent:a:t": _output("a", artifact=_artifact()),
                                         "sql_agent:b:t": _output("b", status="error")},
                       errors=[verdict.error("executor")])

    assert build_scan_layer_router(fake_runtime())(state) == END


def _write(store, tenant, trace, name, frame):
    return store.write_lazy(frame.lazy(), store.run_metadata(tenant, trace, name)).model_copy(
        update={"sub_query_id": name})


def _aggregate(groups, dropped, rows):
    from nl2sql.pipeline.nodes.aggregator.node import EngineAggregatorNode

    ctx = fake_runtime(settings={"tenant_id": "t1"})
    ids = sorted({i.subquery_id for g in groups for i in g.inputs})
    decomposition = DecomposerResponse(sub_queries=[SubQuery(id=i, datasource_id="shop", intent=i) for i in ids],
                                       combine_groups=groups)
    refs = {i: _write(ctx.artifact_store, "t1", "t", i, rows[i]) for i in ids if i not in dropped}
    outputs = {f"sql_agent:{i}:t": _output(i, status="dropped" if i in dropped else "success",
                                           artifact=refs.get(i)) for i in ids}
    state = GraphState(user_query="q", trace_id="t", decomposer_response=decomposition,
                       execution_dag=build_execution_dag(decomposition), artifact_refs=refs,
                       subgraph_outputs=outputs)
    return EngineAggregatorNode(ctx)(state)


def test_sub_query_deny_in_a_join_group_drops_the_group():
    rows = {"a": pl.DataFrame({"id": [1, 2], "x": [10, 20]}), "b": pl.DataFrame({"id": [1], "y": [5]}),
            "c": pl.DataFrame({"n": [3]})}
    groups = [CombineGroup(group_id="pair", operation="join", join_keys=[{"left": "id", "right": "id"}],
                           inputs=[CombineInput(subquery_id="a", role="left"),
                                   CombineInput(subquery_id="b", role="right")]),
              CombineGroup(group_id="count", operation="standalone", inputs=[CombineInput(subquery_id="c")])]

    out = _aggregate(groups, {"b"}, rows)

    response = out["aggregator_response"]
    assert not response.errors
    assert list(response.terminal_results) == ["combine_count"]
    [warning] = [w for w in out["warnings"] if w["error_code"] == "HOOK_DENIED"]
    assert "pair" in warning["message"] and "b" in warning["message"]


def test_every_part_dropped_is_an_error_of_the_run():
    rows = {"a": pl.DataFrame({"id": [1]}), "b": pl.DataFrame({"id": [1]})}
    groups = [CombineGroup(group_id="pair", operation="join", join_keys=[{"left": "id", "right": "id"}],
                           inputs=[CombineInput(subquery_id="a", role="left"),
                                   CombineInput(subquery_id="b", role="right")])]

    out = _aggregate(groups, {"b"}, rows)

    assert out["aggregator_response"].errors[0].error_code == ErrorCode.HOOK_DENIED


def test_a_dropped_sub_query_goes_on_to_the_aggregator():
    from nl2sql.pipeline.routes import build_scan_layer_router

    decomposition = DecomposerResponse(
        sub_queries=[SubQuery(id="a", datasource_id="shop", intent="a")],
        combine_groups=[CombineGroup(group_id="ga", operation="standalone", inputs=[CombineInput(subquery_id="a")])])
    state = GraphState(user_query="q", decomposer_response=decomposition,
                       execution_dag=build_execution_dag(decomposition),
                       subgraph_outputs={"sql_agent:a:t": _output("a", status="dropped")})

    [send] = build_scan_layer_router(fake_runtime())(state)
    assert send.node == "aggregator"


# ---------------------------------------------------------------- answer.after


def _synthesizer(hooks):
    from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
    from nl2sql.pipeline.nodes.answer_synthesizer.node import AnswerSynthesizerNode
    from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AggregatedResponse

    ctx = fake_runtime(hooks=hooks, llm_registry=MagicMock())
    node = AnswerSynthesizerNode(ctx)
    node.chain = SimpleNamespace(invoke=lambda inputs: AggregatedResponse(
        summary="Three customers.", format_type="text", content="There are three customers.", warnings=[]))
    state = GraphState(user_query="How many customers?", aggregator_response=AggregatorResponse(
        terminal_results={"combine_g": [{"n": 3}]}),
        warnings=[{"node": "sql_agent", "message": "Sub-query 'b' (salaries) was not run: No payroll",
                   "error_code": "HOOK_DENIED", "severity": "WARNING", "sub_query_id": "b"}])
    return node(state)


def test_answer_after_sees_the_answer_and_may_deny_it():
    hooks = _hooks(lambda ctx: Deny(reason="Answers are reviewed first"))

    out = _synthesizer(hooks)

    [(point, ctx)] = hooks.seen
    assert point == "answer.after" and ctx.final_answer["summary"] == "Three customers."
    assert out["answer_synthesizer_response"].final_answer is None
    assert out["errors"][0].error_code == ErrorCode.HOOK_DENIED


def test_the_answer_says_which_part_a_hook_dropped():
    out = _synthesizer(_hooks(lambda ctx: Allow()))

    warnings = out["answer_synthesizer_response"].final_answer["warnings"]
    assert any("No payroll" in w for w in warnings)
