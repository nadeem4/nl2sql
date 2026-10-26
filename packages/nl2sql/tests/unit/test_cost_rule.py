"""The cost rule: ``policies.cost`` and the built-in ``policy.cost`` hook (C2).

docs/architecture/wave_b.md, section 9, and D52 to D54. The hook asks the
adapter's ``estimate`` once per sub-query at ``execute.before``, ahead of every
configured hook, and answers allow, ``NeedsApproval`` or ``Deny``. The adapter
here is a stand-in with a scripted estimate: no database and no model.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_curated_matcher import CHOOSE_TOP, COUNT, QUESTION, TOP, _ctx, _server
from test_hook_points import _executor, _hooks, _sub_state, db  # noqa: F401
from test_plan_cache import _executed, _plan, _sq, _wrapper_state, store  # noqa: F401
from test_review_queue import ADMIN, ROLES, _engine, _project

from nl2sql.common.errors import FATAL_ERRORS, ErrorCode, ErrorSeverity
from nl2sql.config.models import Policies
from nl2sql.events import EventBus, RunScope
from nl2sql.feedback.corrections import CorrectionRefused
from nl2sql.hooks import Allow, HookBinding, HookRunner
from nl2sql.hooks.approval import Approval, ApprovalRequest
from nl2sql.pipeline.graph_utils import wrap_subgraph
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.pipeline.state import GraphState
from nl2sql.policies import UserContext
from nl2sql.policies.cost import build_cost_guard
from nl2sql.testing.runtime import fake_runtime
from nl2sql_sdk import Estimate

SQL = "SELECT SUM(total) FROM orders"
ANALYST = UserContext(user_id="bob", roles=["analyst"])
ANALYST_ADMIN = UserContext(user_id="cy", roles=["analyst", "admin"])
APPROVER = UserContext(user_id="root", roles=["admin"])
BOTH = {"analyst": {"description": "a", "allow": ["shop"]}, "admin": {"description": "b", "allow": ["*"]}}


class _Adapter:
    """Answers ``estimate`` from a script: a number of rows, ``None`` (no rows) or an exception."""

    def __init__(self, rows=12_400_000):
        self.rows = rows
        self.calls = []

    def estimate(self, sql, parameters):
        self.calls.append((sql, parameters))
        if isinstance(self.rows, Exception):
            raise self.rows
        return Estimate(rows=self.rows, cost=3.5, source="test plan")


def _registry(adapter, declares=True):
    return SimpleNamespace(supports=lambda ds, *caps: declares, get_adapter=lambda ds: adapter)


def _guard(policies, adapter=None, declares=True):
    adapter = adapter or _Adapter()
    guard = build_cost_guard(Policies.model_validate(policies), _registry(adapter, declares))
    return guard, adapter


def _ask(guard, user=ANALYST, sql=SQL, approvals=(), params=None):
    runner = HookRunner([guard.binding()], admin_roles=["admin"])
    return runner.call("execute.before", trace_id="t1", question="q", user=user, datasource_id="shop",
                       sub_query=_sq(), sql=sql, params=params, approvals=list(approvals))


def _approval(user=ANALYST, sql=SQL):
    request = ApprovalRequest.build(question="q", asker=user, hook="policy.cost", datasource_id="shop", sql=sql,
                                    reason="x")
    return Approval(request=request, by=APPROVER)


# ---------------------------------------------------------------- when it asks


def test_no_cost_block_never_estimates():
    assert build_cost_guard(Policies.model_validate({"allow_all": True}), _registry(_Adapter())) is None
    assert build_cost_guard(Policies.model_validate({"roles": BOTH}), _registry(_Adapter())) is None


def test_no_limit_never_estimates():
    guard, adapter = _guard({"allow_all": True, "cost": {"over_limit": "refuse"}})

    assert _ask(guard).allowed
    assert adapter.calls == []


def test_within_limit_runs():
    guard, adapter = _guard({"allow_all": True, "cost": {"max_estimated_rows": 20_000_000, "over_limit": "refuse"}})

    assert _ask(guard).allowed
    assert adapter.calls == [(SQL, {})]


def test_values_are_passed_to_the_estimate():
    guard, adapter = _guard({"allow_all": True, "cost": {"max_estimated_rows": 20_000_000}})

    _ask(guard, params={"n": 2})

    assert adapter.calls == [(SQL, {"n": 2})]


# ---------------------------------------------------------------- over the limit


def test_confirm_ends_needs_approval_and_an_approval_runs_it():
    guard, _ = _guard({"allow_all": True, "cost": {"max_estimated_rows": 5_000_000, "over_limit": "confirm"}})

    verdict = _ask(guard)

    assert not verdict.allowed and verdict.hook == "policy.cost"
    assert verdict.decision.reason == "Estimated to read 12,400,000 rows (limit 5,000,000)"
    assert verdict.error("executor").error_code == ErrorCode.NEEDS_APPROVAL
    assert _ask(guard, approvals=[_approval()]).allowed  # the same SQL, approved by an admin
    other = _ask(guard, sql="SELECT 1", approvals=[_approval()])
    assert not other.allowed  # an approval covers the SQL it was made for


def test_an_approval_by_a_non_admin_does_not_count():
    guard, _ = _guard({"allow_all": True, "cost": {"max_estimated_rows": 5_000_000}})
    approval = _approval().model_copy(update={"by": ANALYST})

    assert not _ask(guard, approvals=[approval]).allowed


def test_refuse_is_cost_limit_exceeded_and_not_retried():
    guard, _ = _guard({"roles": {"analyst": {"description": "a", "allow": ["shop"],
                                             "cost": {"max_estimated_rows": 1_000_000, "over_limit": "refuse"}}}})

    error = _ask(guard).error("executor")

    assert error.error_code == ErrorCode.COST_LIMIT_EXCEEDED
    assert error.severity == ErrorSeverity.CRITICAL and error.error_code in FATAL_ERRORS and not error.is_retryable
    assert error.message == ("This query is estimated to read 12,400,000 rows, over this role's limit of 1,000,000 "
                             "(policies.roles.analyst.cost). Ask about less data, for example a shorter period.")
    assert error.details["hook"] == "policy.cost"


def test_the_top_level_limit_is_named_when_no_role_sets_it():
    guard, _ = _guard({"allow_all": True, "cost": {"max_estimated_rows": 1_000, "over_limit": "refuse"}})

    assert "(policies.cost)" in _ask(guard).error("executor").message


# ---------------------------------------------------------------- no estimate


@pytest.mark.parametrize("action, ended", [("allow", None), ("confirm", ErrorCode.NEEDS_APPROVAL),
                                           ("refuse", ErrorCode.COST_LIMIT_EXCEEDED)])
@pytest.mark.parametrize("failure", ["raises", "no rows", "not declared"])
def test_unknown_follows_its_action(action, ended, failure):
    adapter = _Adapter(rows={"raises": RuntimeError("no SHOWPLAN permission"), "no rows": None}.get(failure, 5))
    guard, _ = _guard({"allow_all": True, "cost": {"max_estimated_rows": 10, "unknown": action}}, adapter,
                      declares=failure != "not declared")

    verdict = _ask(guard)

    if ended is None:
        assert verdict.allowed
    else:
        error = verdict.error("executor")
        assert error.error_code == ended
        assert "no SHOWPLAN" not in error.message  # the database's text is operator data


def test_unknown_allows_by_default():
    guard, _ = _guard({"allow_all": True, "cost": {"max_estimated_rows": 10}}, _Adapter(RuntimeError("down")))

    assert _ask(guard).allowed


# ---------------------------------------------------------------- which rule


def test_a_role_replaces_only_the_fields_it_sets():
    guard, _ = _guard({"cost": {"max_estimated_rows": 5_000_000, "over_limit": "confirm", "unknown": "refuse"},
                       "roles": {"analyst": {"description": "a", "allow": ["shop"],
                                             "cost": {"max_estimated_rows": 1_000_000, "over_limit": "refuse"}}}},
                      _Adapter(RuntimeError("down")))

    assert _ask(guard).error("executor").error_code == ErrorCode.COST_LIMIT_EXCEEDED  # unknown: refuse, inherited


def test_most_permissive_value_per_field_and_null_wins():
    cost = {"max_estimated_rows": 100}
    roles = {"analyst": {"description": "a", "allow": ["shop"], "cost": {"max_estimated_rows": 1_000_000,
                                                                          "over_limit": "refuse"}},
             "admin": {"description": "b", "allow": ["*"], "cost": {"max_estimated_rows": 5_000_000,
                                                                    "over_limit": "confirm"}}}
    guard, adapter = _guard({"cost": cost, "roles": roles})

    # Highest limit (5,000,000) and the weakest action (confirm), from different roles.
    verdict = _ask(guard, ANALYST_ADMIN)
    assert verdict.decision.reason == "Estimated to read 12,400,000 rows (limit 5,000,000)"

    roles["admin"]["cost"] = {"max_estimated_rows": None}
    guard, adapter = _guard({"cost": cost, "roles": roles})
    assert _ask(guard, ANALYST_ADMIN).allowed and adapter.calls == []  # null: no limit, so no estimate


def test_a_role_without_a_cost_block_gets_the_top_level_rule():
    roles = {"analyst": {"description": "a", "allow": ["shop"], "cost": {"max_estimated_rows": 1_000_000}},
             "ops": {"description": "o", "allow": ["shop"]}}
    guard, _ = _guard({"cost": {"max_estimated_rows": 50_000_000}, "roles": roles})

    ops = UserContext(user_id="o", roles=["ops"])
    assert _ask(guard, ops).allowed
    assert not _ask(guard, ANALYST).allowed


def test_cost_rule_applies_under_allow_all():
    guard, _ = _guard({"allow_all": True, "cost": {"max_estimated_rows": 1_000, "over_limit": "refuse"}})

    assert _ask(guard, UserContext()).error("executor").error_code == ErrorCode.COST_LIMIT_EXCEEDED


def test_the_default_role_is_the_callers_when_she_has_none():
    guard, _ = _guard({"default_role": "analyst", "cost": {"max_estimated_rows": 50_000_000},
                       "roles": {"analyst": {"description": "a", "allow": ["shop"],
                                             "cost": {"max_estimated_rows": 1_000, "over_limit": "refuse"}}}})

    assert not _ask(guard, UserContext()).allowed


# ---------------------------------------------------------------- where it runs


def test_no_datasource_is_an_unknown():
    guard, adapter = _guard({"allow_all": True, "cost": {"max_estimated_rows": 1}})

    verdict = HookRunner([guard.binding()]).call("execute.before", trace_id="t", question="q", user=ANALYST,
                                                 datasource_id=None, sub_query=_sq(), sql=SQL)

    assert adapter.calls == [] and verdict.allowed  # nothing to estimate against: unknown, which allows


def test_cost_hook_runs_before_configured_hooks(tmp_path):
    seen = []
    config = _project(tmp_path, policies={"roles": ROLES, "cost": {"max_estimated_rows": 10}})
    config = config.model_copy(update={"hooks": [lambda ctx: seen.append(ctx.point) or Allow()]})
    with _engine(config) as engine:
        runner = engine._runtime.hooks
        assert [b.name for b in runner.bindings][0] == "policy.cost"
        assert len(runner.bindings) == 2
        assert runner.approver_roles("policy.cost") == tuple(config.policies.admin_roles)


def test_no_cost_block_adds_no_hook(tmp_path):
    with _engine(_project(tmp_path)) as engine:
        assert engine._runtime.hooks.bindings == ()


# ---------------------------------------------------------------- the generated, verified and corrected paths


def _verified(db, guard):
    server = _server(CHOOSE_TOP)
    try:
        ctx = _ctx(db, server, [TOP, COUNT])
        ctx.hooks = HookRunner([guard.binding()], admin_roles=["admin"])
        from nl2sql.pipeline.nodes.curated_matcher import CuratedMatcherNode

        return CuratedMatcherNode(ctx)(GraphState(user_query=QUESTION, user_context=UserContext(roles=["analyst"])))
    finally:
        server.stop()


def _node(db, guard):  # noqa: F811
    return _executor(db, HookRunner([guard.binding()], admin_roles=["admin"]))


def test_estimate_is_on_the_result_and_published(db):  # noqa: F811
    guard, _ = _guard({"allow_all": True, "cost": {"max_estimated_rows": 20_000_000}})
    bus = EventBus()
    events = []
    bus.subscribe(events.append)

    with RunScope(bus, question="q").entered():
        out = _node(db, guard)(_sub_state())

    assert out["executor_response"].artifact.row_count == 3
    assert out["estimate"].rows == 12_400_000 and out["estimate"].source == "test plan"
    [event] = [e for e in events if e.name == "cost.estimated"]
    assert event.data == {"datasource_id": "shop", "sub_query_id": "sq1", "estimated_rows": 12_400_000,
                          "cost": 3.5, "limit": 20_000_000, "decision": "allow"}


def test_a_refused_sub_query_still_reports_its_estimate(db):  # noqa: F811
    guard, _ = _guard({"allow_all": True, "cost": {"max_estimated_rows": 1_000, "over_limit": "refuse"}})

    out = _node(db, guard)(_sub_state())

    assert out["executor_response"] is None
    assert out["errors"][0].error_code == ErrorCode.COST_LIMIT_EXCEEDED
    assert out["estimate"].rows == 12_400_000


def test_no_guard_means_no_estimate_on_the_result(db):  # noqa: F811
    out = _executor(db, _hooks(lambda ctx: Allow()))(_sub_state())

    assert "estimate" not in out or out["estimate"] is None


def test_the_estimate_reaches_the_sub_queries_output(store):  # noqa: F811
    estimate = Estimate(rows=7, source="x")

    out = wrap_subgraph(SimpleNamespace(invoke=lambda state, config=None: {**state, **{
        "ast_planner_response": ASTPlannerResponse(plan=_plan()),
        "generator_response": GeneratorResponse(sql_draft="SELECT 1"),
        "executor_response": _executed(),
        "estimate": estimate,
    }}), "sql_agent", fake_runtime(state=store))(_wrapper_state())

    assert out["subgraph_outputs"]["sql_agent:sq1:t1"].estimate == estimate


def test_verified_query_is_estimated_too(db):  # noqa: F811
    guard, adapter = _guard({"allow_all": True, "cost": {"max_estimated_rows": 20_000_000}})

    out = _verified(db, guard)

    [output] = out["subgraph_outputs"].values()
    assert output.estimate.rows == 12_400_000
    assert adapter.calls[0][1] == {"n": 2, "year": 2012}  # the values the query was bound to


def test_verified_query_over_the_limit_is_stopped(db):  # noqa: F811
    guard, _ = _guard({"allow_all": True, "cost": {"max_estimated_rows": 10, "over_limit": "refuse"}})

    out = _verified(db, guard)

    [output] = out["subgraph_outputs"].values()
    assert output.errors[0].error_code == ErrorCode.COST_LIMIT_EXCEEDED and output.estimate.rows == 12_400_000


def _cost_engine(tmp_path, monkeypatch, over_limit, rows=500):
    engine = _engine(_project(tmp_path, policies={"roles": ROLES, "cost": {"max_estimated_rows": 100,
                                                                           "over_limit": over_limit}}))
    assert engine.index.build().ok
    registry = engine._runtime.ds_registry
    real = registry.supports
    monkeypatch.setattr(registry, "supports", lambda ds, *caps: any(
        getattr(c, "value", c) == "estimates_cost" for c in caps) or real(ds, *caps))
    adapter = registry.get_adapter("shop")
    monkeypatch.setattr(adapter, "estimate", _Adapter(rows).estimate, raising=False)
    return engine


@pytest.mark.parametrize("over_limit, code", [("confirm", "needs_approval"), ("refuse", "hook_denied")])
def test_correction_check_is_cost_guarded(tmp_path, monkeypatch, over_limit, code):
    from test_corrections import FIX, _run

    with _cost_engine(tmp_path, monkeypatch, over_limit) as engine:
        with pytest.raises(CorrectionRefused) as error:
            engine.feedback.correct(_run(engine), sql=FIX, user_context=ADMIN)

        assert error.value.code == code
        assert "500" in str(error.value)
        assert not engine.proposals.list(user_context=ADMIN)


# ---------------------------------------------------------------- how the run ends


def test_a_refused_cost_ends_the_run_after_its_layer(store):  # noqa: F811
    from langgraph.graph import END
    from test_hook_points import _artifact, _output

    from nl2sql.pipeline.nodes.decomposer.dag import build_execution_dag
    from nl2sql.pipeline.nodes.decomposer.schemas import CombineGroup, CombineInput, DecomposerResponse, SubQuery
    from nl2sql.pipeline.routes import build_scan_layer_router

    decomposition = DecomposerResponse(
        sub_queries=[SubQuery(id="a", datasource_id="shop", intent="a"), SubQuery(id="b", datasource_id="shop", intent="b")],
        combine_groups=[CombineGroup(group_id="ga", operation="standalone", inputs=[CombineInput(subquery_id="a")]),
                        CombineGroup(group_id="gb", operation="standalone", inputs=[CombineInput(subquery_id="b")])])
    guard, _ = _guard({"allow_all": True, "cost": {"max_estimated_rows": 1, "over_limit": "refuse"}})
    state = GraphState(user_query="q", decomposer_response=decomposition,
                       execution_dag=build_execution_dag(decomposition), artifact_refs={"a": _artifact()},
                       subgraph_outputs={"sql_agent:a:t": _output("a", artifact=_artifact()),
                                         "sql_agent:b:t": _output("b", status="error")},
                       errors=[_ask(guard).error("executor")])

    assert build_scan_layer_router(fake_runtime())(state) == END


def test_a_refused_cost_withholds_data_and_the_result_carries_the_estimate():
    from nl2sql.api.query_api import result_from_state
    from nl2sql.pipeline.subgraphs.schemas import SubgraphOutput

    guard, _ = _guard({"allow_all": True, "cost": {"max_estimated_rows": 1, "over_limit": "refuse"}})
    error = _ask(guard).error("executor")
    output = SubgraphOutput(sub_query=_sq(), subgraph_id="sql_agent:sq1:t1", errors=[error], status="error",
                            estimate=Estimate(rows=12_400_000, cost=3.5, source="test plan"))

    result = result_from_state({"errors": [error], "subgraph_outputs": {output.subgraph_id: output}})

    assert result.errors[0]["error_code"] == "COST_LIMIT_EXCEEDED"
    assert result.sub_queries[0].estimate.rows == 12_400_000 and result.sub_queries[0].rows is None
