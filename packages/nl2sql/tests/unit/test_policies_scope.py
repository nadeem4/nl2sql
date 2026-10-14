"""Policies, step 1: allow and deny globs over ``datasource.schema.table``, and one ``Scope``.

The pattern rules are in docs/architecture/phase2.md, "Policies, step 1", and
docs/configuration/policies.md. Every place that decides table access asks a
``Scope``, so these tests pin the decision itself; the nodes are pinned in
``test_policies_in_the_pipeline.py``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from nl2sql.config import Policies, Role
from nl2sql.policies import PolicyEngine, Scope, UserContext


def _engine(roles: dict, ds_registry=None, **policies) -> PolicyEngine:
    return PolicyEngine(
        Policies(roles={name: Role(description=name, **role) for name, role in roles.items()}, **policies),
        ds_registry,
    )


def _scope(roles: dict, user_roles, datasource_id: str = "sales", **policies) -> Scope:
    return _engine(roles, **policies).scope(UserContext(roles=user_roles), datasource_id)


ANALYST = {"allow": ["sales"], "deny": ["sales.audit.*", "sales.*.*_pii"]}


# --- the four rules the PR plan names ----------------------------------------------


def test_deny_beats_allow_within_a_role():
    scope = _scope({"analyst": ANALYST}, ["analyst"])

    assert scope.allows("public", "orders")
    assert not scope.allows("audit", "log")
    assert not scope.allows("public", "customers_pii")
    assert scope.rule_for("public", "orders") is None
    assert "sales.audit.*" in scope.rule_for("audit", "log")
    assert "sales.*.*_pii" in scope.rule_for("public", "customers_pii")


def test_union_across_roles_keeps_each_roles_deny():
    roles = {
        "analyst": ANALYST,
        "auditor": {"allow": ["sales.audit.log"]},
        "reporter": {"allow": ["sales.reporting.*"], "deny": ["sales.public.*"]},
    }

    # A table is readable when some role allows it and that same role does not deny it.
    both = _scope(roles, ["analyst", "auditor"])
    assert both.allows("audit", "log")          # the auditor allows it; the analyst's deny is the analyst's
    assert not both.allows("audit", "secrets")  # the analyst denies it, and the auditor never allowed it

    # A role's deny never takes away what another role allows.
    with_reporter = _scope(roles, ["analyst", "reporter"])
    assert with_reporter.allows("public", "orders")
    assert with_reporter.allows("reporting", "daily")
    assert not with_reporter.allows("public", "customers_pii")


@pytest.mark.parametrize("field", ["allow", "deny"])
def test_two_part_pattern_is_rejected_with_the_fix(field):
    with pytest.raises(ValidationError) as caught:
        Role(description="d", **{field: ["sales.orders"]})

    message = str(caught.value)
    assert "'sales.orders' has two parts" in message
    assert "sales.<schema>.orders" in message and "sales.<schema>.*" in message


def test_matching_ignores_case():
    scope = _scope({"r": {"allow": ["Sales.Public.ORDERS", "SALES.reporting.*"], "deny": ["sales.REPORTING.Secret*"]}},
                   ["r"])

    assert scope.allows("public", "orders")
    assert scope.allows("PUBLIC", "Orders")
    assert scope.allows("Reporting", "Daily")
    assert not scope.allows("reporting", "SECRETS")  # a deny cannot be dodged by case
    assert _scope({"r": {"allow": ["sales"]}}, ["r"], datasource_id="SALES").reaches_datasource()


# --- the pattern shape ------------------------------------------------------------


@pytest.mark.parametrize("pattern", ["*", "sales", "sal*", "sales.audit.*", "sales.*.*_pii", "*.public.orders"])
def test_the_pattern_shapes_that_load(pattern):
    Role(description="d", allow=[pattern], deny=[pattern])


@pytest.mark.parametrize("pattern", ["", "sales.", ".orders", "sales..orders", "a.b.c.d"])
def test_malformed_patterns_are_rejected(pattern):
    with pytest.raises(ValidationError) as caught:
        Role(description="d", allow=[pattern])
    assert "'*', 'datasource' or 'datasource.schema.table'" in str(caught.value)


def test_a_two_part_wildcard_suggests_the_whole_datasource():
    with pytest.raises(ValidationError) as caught:
        Role(description="d", allow=["sales.*"])
    message = str(caught.value)
    assert "'sales.*' has two parts" in message and "write 'sales'" in message


def test_the_old_role_fields_are_gone():
    with pytest.raises(ValidationError):
        Role(description="d", allowed_datasources=["sales"], allowed_tables=["sales.public.*"])


# --- datasource access ------------------------------------------------------------


def test_datasource_access_is_implied_by_the_allow_patterns():
    roles = {"viewer": {"allow": ["sales.reporting.*", "support.public.tickets"]}}

    assert _scope(roles, ["viewer"], "sales").reaches_datasource()
    assert _scope(roles, ["viewer"], "support").reaches_datasource()
    assert not _scope(roles, ["viewer"], "hr").reaches_datasource()
    assert _scope({"r": {"allow": ["*"]}}, ["r"], "hr").reaches_datasource()
    assert _scope({"r": {"allow": ["s*"]}}, ["r"], "support").reaches_datasource()


def test_a_deny_of_the_whole_datasource_takes_it_away():
    roles = {"r": {"allow": ["*"], "deny": ["hr"]}}

    assert not _scope(roles, ["r"], "hr").reaches_datasource()
    assert not _scope(roles, ["r"], "hr").allows("public", "salaries")
    assert _scope(roles, ["r"], "sales").reaches_datasource()
    assert not _scope({"r": {"allow": ["*"], "deny": ["hr.*.*"]}}, ["r"], "hr").reaches_datasource()


def test_a_table_outside_every_allowed_datasource_is_refused_with_the_missing_allow():
    scope = _scope({"r": {"allow": ["sales"]}}, ["r"], "hr")
    assert not scope.allows("public", "salaries")
    assert scope.rule_for("public", "salaries") == "no allow pattern of role(s) 'r' matches 'hr.public.salaries'"


# --- who the caller is ------------------------------------------------------------


def test_allow_all_allows_everything():
    scope = PolicyEngine(Policies(allow_all=True)).scope(UserContext(), "anything")
    assert scope.reaches_datasource() and scope.allows("any", "table")


def test_no_roles_gets_the_default_role():
    engine = _engine({"viewer": {"allow": ["sales.reporting.*"]}}, default_role="viewer")
    assert engine.scope(UserContext(), "sales").allows("reporting", "daily")
    assert engine.scope(None, "sales").allows("reporting", "daily")


@pytest.mark.parametrize("roles", [[], ["ghost"]], ids=["no-role", "unknown-role"])
def test_no_role_or_an_unknown_role_reads_nothing(roles):
    scope = _scope({"analyst": ANALYST}, roles)
    assert not scope.reaches_datasource()
    assert not scope.allows("public", "orders")


# --- the default schema -----------------------------------------------------------


def _registry(default):
    adapter = SimpleNamespace(default_schema=lambda: default)
    return SimpleNamespace(get_adapter=lambda _id: adapter)


def test_a_table_with_no_schema_is_in_the_adapters_default_schema():
    engine = _engine({"r": {"allow": ["sales.dbo.orders"]}}, ds_registry=_registry("dbo"))
    scope = engine.scope(UserContext(roles=["r"]), "sales")

    assert scope.default_schema == "dbo"
    assert scope.allows(None, "orders")
    assert not scope.allows("audit", "orders")


def test_main_stands_in_when_the_adapter_cannot_say():
    for registry in (None, SimpleNamespace(), _registry(None), SimpleNamespace(get_adapter=lambda _id: 1 / 0)):
        scope = _engine({"r": {"allow": ["sales.main.orders"]}}, ds_registry=registry).scope(
            UserContext(roles=["r"]), "sales")
        assert scope.default_schema == "main"
        assert scope.allows(None, "orders")


# --- a run pins its scopes --------------------------------------------------------


def test_a_run_builds_each_datasources_scope_once():
    asked = []

    class Adapter:
        def default_schema(self):
            asked.append(1)
            return "main"

    registry = SimpleNamespace(get_adapter=lambda _id: Adapter())
    engine = _engine({"r": {"allow": ["sales"]}}, ds_registry=registry)
    user = UserContext(roles=["r"])

    with engine.run():
        first = engine.scope(user, "sales")
        assert engine.scope(user, "sales") is first
        assert engine.scope(UserContext(roles=["r"]), "sales") is first
        assert engine.scope(user, "support") is not first
    assert len(asked) == 2

    # Outside a run (and in the next run) each call builds afresh.
    assert engine.scope(user, "sales") is not first
    with engine.run():
        assert engine.scope(user, "sales") is not first
