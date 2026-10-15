"""The hook runner: order, decisions, failing closed, and how a hook is built (docs/architecture/wave_a.md, section 6).

Hooks run in the order the runtime hands them over. The first deny ends the
point; modifications chain. An exception, a value that is not a decision, or
a decision the point does not allow is a hook error, which denies with
``HOOK_FAILED``; ``on_error: open`` lets an exception through as an allow,
never a disallowed decision. Every call publishes ``hook.decision``.
"""
from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import BaseModel, ValidationError

from nl2sql.common.errors import ErrorCode, ErrorSeverity
from nl2sql.events import EventBus, RunScope
from nl2sql.hooks import (HOOK_POINTS, Allow, Deny, HookBinding, HookContext, HookPoint, HookRunner, Modify,
                          NeedsApproval, build_hook)
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.policies import UserContext
from nl2sql.registry import Registry
from nl2sql.secrets import SecretManager
from nl2sql.testing.config import make_config

USER = UserContext(user_id="ada", roles=["analyst"])
ALL_POINTS = frozenset(HOOK_POINTS)


def _bind(name, fn, points=ALL_POINTS, on_error="closed", approver_roles=None):
    return HookBinding(name=name, hook=fn, points=frozenset(points), on_error=on_error,
                       approver_roles=approver_roles)


def _run_before(runner, question="How many customers?"):
    return runner.call("run.before", trace_id="t1", question=question, user=USER)


def _plan(table="Customer"):
    return PlanModel.model_validate({"tables": [{"name": table, "alias": "t1"}],
                                     "select_items": [{"expr": {"kind": "column", "alias": "t1",
                                                                "column_name": "id"}}]})


def test_hooks_run_in_config_order():
    seen = []
    runner = HookRunner([_bind("first", lambda ctx: seen.append("first") or Allow()),
                         _bind("second", lambda ctx: seen.append("second") or Allow())])

    verdict = _run_before(runner)

    assert verdict.allowed
    assert seen == ["first", "second"]


def test_first_deny_ends_the_point():
    seen = []
    runner = HookRunner([_bind("gate", lambda ctx: seen.append("gate") or Deny(reason="Not on Sundays")),
                         _bind("later", lambda ctx: seen.append("later") or Allow())])

    verdict = _run_before(runner)

    assert not verdict.allowed
    assert seen == ["gate"]
    error = verdict.error("orchestrator")
    assert error.error_code == ErrorCode.HOOK_DENIED
    assert error.message == "Not on Sundays"
    assert error.severity == ErrorSeverity.CRITICAL and not error.is_retryable
    assert error.details == {"hook": "gate", "point": "run.before", "scope": "run"}


def test_modify_chains():
    runner = HookRunner([_bind("one", lambda ctx: Modify(question=ctx.question + " in 2012")),
                         _bind("two", lambda ctx: Modify(question=ctx.question.upper()))])

    verdict = _run_before(runner, "top customers")

    assert verdict.allowed
    assert verdict.ctx.question == "TOP CUSTOMERS IN 2012"


def test_hook_exception_denies():
    def broken(ctx):
        raise RuntimeError("review service down")

    after = []
    runner = HookRunner([_bind("review", broken), _bind("later", lambda ctx: after.append(1) or Allow())])

    verdict = _run_before(runner)

    assert not verdict.allowed and not after
    error = verdict.error("orchestrator")
    assert error.error_code == ErrorCode.HOOK_FAILED
    # The caller learns which hook failed and where; the exception text is operator data only.
    assert error.message == "The hook 'review' failed at run.before."
    assert error.details == {"hook": "review", "point": "run.before"}


def test_on_error_open_allows_on_exception():
    def broken(ctx):
        raise RuntimeError("review service down")

    runner = HookRunner([_bind("review", broken, on_error="open"),
                         _bind("rewrite", lambda ctx: Modify(question="rewritten"))])

    verdict = _run_before(runner)

    assert verdict.allowed
    assert verdict.ctx.question == "rewritten"


@pytest.mark.parametrize("returned", [
    Modify(plan=None, question="changed"),  # Modify at execute.before
    NeedsApproval(reason="ask"),  # not here: only execute.before may ask, and this is execute.after
    "allow",  # not a decision
])
def test_disallowed_decision_denies_even_when_open(returned):
    point: HookPoint = "execute.before" if isinstance(returned, Modify) else "execute.after"
    runner = HookRunner([_bind("odd", lambda ctx: returned, on_error="open")])

    verdict = runner.call(point, trace_id="t1", question="q", user=USER, datasource_id="shop", sql="SELECT 1")

    assert not verdict.allowed
    assert verdict.error("executor").error_code == ErrorCode.HOOK_FAILED


def test_needs_approval_outside_execute_before_is_a_hook_error():
    runner = HookRunner([_bind("ask", lambda ctx: NeedsApproval(reason="ask a person"))])

    verdict = runner.call("plan.after", trace_id="t1", question="q", user=USER, plan=_plan())

    assert verdict.error("astplanner").error_code == ErrorCode.HOOK_FAILED


def test_modify_of_a_field_the_point_does_not_change_is_a_hook_error():
    runner = HookRunner([_bind("planner", lambda ctx: Modify(plan=_plan("Secret")))])

    verdict = _run_before(runner)

    assert verdict.error("orchestrator").error_code == ErrorCode.HOOK_FAILED


def test_sub_query_deny_outside_a_per_sub_query_point_is_a_hook_error():
    runner = HookRunner([_bind("gate", lambda ctx: Deny(reason="no", scope="sub_query"))])

    assert _run_before(runner).error("orchestrator").error_code == ErrorCode.HOOK_FAILED


def test_sub_query_deny_names_its_scope():
    runner = HookRunner([_bind("gate", lambda ctx: Deny(reason="No payroll", scope="sub_query"))])
    sub_query = SubQuery(id="sq1", datasource_id="hr", intent="salaries")

    verdict = runner.call("execute.before", trace_id="t1", question="q", user=USER, datasource_id="hr",
                          sub_query=sub_query, sql="SELECT 1")

    assert verdict.scope == "sub_query"
    assert verdict.error("executor").details["scope"] == "sub_query"


def test_datasources_after_cannot_add_a_datasource():
    runner = HookRunner([_bind("router", lambda ctx: Modify(datasource_ids=["chinook", "payroll"]))])

    verdict = runner.call("datasources.after", trace_id="t1", question="q", user=USER,
                          datasource_ids=["chinook", "support"])

    assert verdict.error("datasource_resolver").error_code == ErrorCode.HOOK_FAILED


def test_datasources_after_may_remove_a_datasource():
    runner = HookRunner([_bind("router", lambda ctx: Modify(datasource_ids=["chinook"]))])

    verdict = runner.call("datasources.after", trace_id="t1", question="q", user=USER,
                          datasource_ids=["chinook", "support"])

    assert verdict.allowed and verdict.ctx.datasource_ids == ["chinook"]


def test_a_hook_runs_only_at_its_points():
    seen = []
    runner = HookRunner([_bind("sql_only", lambda ctx: seen.append(ctx.point) or Allow(),
                               points=["execute.before"])])

    _run_before(runner)
    runner.call("execute.before", trace_id="t1", question="q", user=USER, sql="SELECT 1")

    assert seen == ["execute.before"]


def test_the_context_is_frozen_and_carries_the_conversation_id():
    ctx = HookContext(point="run.before", trace_id="t1", question="q", user=USER, conversation_id="c1")

    assert ctx.conversation_id == "c1"
    assert HookContext(point="run.before", trace_id="t1", question="q", user=USER).conversation_id is None
    assert ctx.approvals == []
    with pytest.raises(ValidationError):
        ctx.question = "other"


def test_every_call_publishes_hook_decision():
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    scope = RunScope(bus, question="q")
    runner = HookRunner([_bind("ok", lambda ctx: Allow()),
                         _bind("rewrite", lambda ctx: Modify(question="better")),
                         _bind("gate", lambda ctx: Deny(reason="No"))])

    with scope.entered():
        _run_before(runner)

    decisions = [(e.data["hook"], e.data["decision"], e.data["reason"]) for e in seen if e.name == "hook.decision"]
    assert decisions == [("ok", "allow", None), ("rewrite", "modify", None), ("gate", "deny", "No")]
    assert all(e.trace_id == scope.trace_id for e in seen)


def test_a_hook_failure_keeps_its_reason_in_the_event_not_the_error():
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)

    def broken(ctx):
        raise RuntimeError("https://review.internal/?token=abc")

    with RunScope(bus, question="q").entered():
        verdict = _run_before(HookRunner([_bind("review", broken)]))

    [event] = [e for e in seen if e.name == "hook.decision"]
    assert "token=abc" in event.data["reason"]
    assert "token=abc" not in verdict.error("orchestrator").message


def test_an_exception_let_through_is_published_as_an_error():
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)

    def broken(ctx):
        raise RuntimeError("down")

    with RunScope(bus, question="q").entered():
        _run_before(HookRunner([_bind("review", broken, on_error="open")]))

    [event] = [e for e in seen if e.name == "hook.decision"]
    assert event.data["decision"] == "error" and "down" in event.data["reason"]


def test_no_hooks_allow_everything():
    verdict = _run_before(HookRunner([]))
    assert verdict.allowed and verdict.ctx.question == "How many customers?"


def test_the_point_table_says_who_may_modify():
    assert {p for p, rule in HOOK_POINTS.items() if Modify in rule.decisions} == {
        "run.before", "datasources.after", "plan.after"}
    assert {p for p, rule in HOOK_POINTS.items() if NeedsApproval in rule.decisions} == {"execute.before"}


# ---------------------------------------------------------------- building a hook


class _ReviewConfig(BaseModel):
    model_config = {"extra": "forbid"}
    url: str


class ReviewHook:
    interface_version: ClassVar[int] = 1
    config_model: ClassVar[type] = _ReviewConfig
    points: ClassVar[frozenset] = frozenset({"execute.before"})

    def __init__(self, url):
        self.url = url

    @classmethod
    def from_config(cls, cfg):
        return cls(cfg.url)

    def __call__(self, ctx):
        return Allow()


def _registry():
    registry = Registry.default()
    registry.add_hook("corp_sql_review", ReviewHook)
    return registry


def test_a_hook_block_is_built_through_the_registry():
    from nl2sql.config import HookConfig

    binding = build_hook(HookConfig(type="corp_sql_review", url="https://review", approver_roles=["dba"]),
                         registry=_registry(), secrets=SecretManager(Registry.default()))

    assert binding.name == "corp_sql_review"
    assert binding.hook.url == "https://review"
    assert binding.points == frozenset({"execute.before"})
    assert binding.on_error == "closed" and binding.approver_roles == ("dba",)


def test_a_hook_object_is_used_as_given():
    def stamp(ctx):
        return Allow()

    binding = build_hook(stamp, registry=_registry(), secrets=SecretManager(Registry.default()))

    assert binding.hook is stamp and binding.name == "stamp" and binding.points == ALL_POINTS


def test_unknown_point_is_a_config_error(tmp_path):
    from nl2sql.config import ConfigError
    from nl2sql.runtime import Runtime

    config = make_config(tmp_path, hooks=[{"type": "corp_sql_review", "points": ["plan.after"], "url": "u"}])

    with pytest.raises(ConfigError) as caught:
        Runtime.components(config, _registry())
    assert "hooks[0]" in str(caught.value)
    assert "plan.after" in str(caught.value)


def test_a_point_that_does_not_exist_is_a_config_error(tmp_path):
    from nl2sql.config import ConfigError

    with pytest.raises((ConfigError, ValidationError)):
        make_config(tmp_path, hooks=[{"type": "corp_sql_review", "points": ["plan.before"]}])


def test_the_runtime_builds_the_runner_in_config_order(tmp_path):
    from nl2sql.runtime import Runtime

    config = make_config(tmp_path, hooks=[{"type": "corp_sql_review", "url": "u"},
                                          {"type": "corp_sql_review", "url": "v", "on_error": "open"}])
    runtime = Runtime.components(config, _registry())
    try:
        assert [b.hook.url for b in runtime.hooks.bindings] == ["u", "v"]
        assert [b.on_error for b in runtime.hooks.bindings] == ["closed", "open"]
    finally:
        runtime.close()


def test_a_built_in_hook_can_run_first_with_its_own_approvers():
    # Wave B's policy.cost: the runtime hands the runner its list, built-ins first.
    seen = []
    builtin = _bind("policy.cost", lambda ctx: seen.append("cost") or Allow(), points=["execute.before"],
                    approver_roles=("admin",))
    configured = _bind("review", lambda ctx: seen.append("review") or Allow())

    runner = HookRunner([builtin, configured])
    runner.call("execute.before", trace_id="t", question="q", user=USER, sql="SELECT 1")

    assert seen == ["cost", "review"]
    assert runner.bindings[0].approver_roles == ("admin",)
