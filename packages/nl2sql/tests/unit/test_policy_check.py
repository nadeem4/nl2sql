"""Policies, step 1: the ``PolicyCheck`` extension point (PR P2).

A check is a plugin that grants allow and deny patterns per caller and
datasource. Its ``Grant`` is intersected with the YAML roles, so neither can
widen the other; an exception from it denies the datasource for the run; it is
asked once per caller and datasource per run. See docs/architecture/phase2.md,
"The ``PolicyCheck`` extension point", and docs/configuration/policies.md.
"""
from __future__ import annotations

import logging
from typing import ClassVar, List, Tuple

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from nl2sql.config import Policies, Role
from nl2sql.policies import PolicyEngine, UserContext
from nl2sql.registry import Registry, RegistryError
from nl2sql.testing.config import make_config
from nl2sql_sdk.policy import Grant, PolicyCheck


class FixedCheck:
    """Grants the same patterns to everyone, and records who it was asked about."""

    interface_version: ClassVar[int] = 1

    class config_model(BaseModel):
        model_config = ConfigDict(extra="forbid")
        allow: List[str] = []
        deny: List[str] = []

    def __init__(self, allow=(), deny=()):
        self.allow, self.deny = list(allow), list(deny)
        self.asked: List[Tuple[str, str]] = []

    @classmethod
    def from_config(cls, cfg):
        return cls(cfg.allow, cfg.deny)

    def grant(self, user: UserContext, datasource_id: str) -> Grant:
        self.asked.append((user.user_id, datasource_id))
        return Grant(allow=self.allow, deny=self.deny)


class BrokenCheck(FixedCheck):
    def grant(self, user, datasource_id):
        raise TimeoutError("the IAM service did not answer")


def _engine(check, roles=None, **policies) -> PolicyEngine:
    roles = {name: Role(description=name, **role) for name, role in (roles or {}).items()}
    return PolicyEngine(Policies(roles=roles, check=check, **policies))


ANALYST = {"analyst": {"allow": ["sales"], "deny": ["sales.audit.*"]}}
USER = UserContext(user_id="ana", roles=["analyst"])


# --- the five tests the PR plan names --------------------------------------------


def test_check_cannot_widen_yaml_roles():
    # The check grants everything; the role still denies the audit schema and never reaches support.
    engine = _engine(FixedCheck(allow=["*"]), ANALYST)

    sales = engine.scope(USER, "sales")
    assert sales.allows("public", "orders")
    assert not sales.allows("audit", "log")
    assert "sales.audit.*" in sales.rule_for("audit", "log")
    assert not engine.scope(USER, "support").reaches_datasource()


def test_yaml_roles_cannot_widen_the_check():
    # The role allows all of sales; the check allows only the reporting schema, and denies one table in it.
    check = FixedCheck(allow=["sales.reporting.*"], deny=["sales.reporting.salaries"])
    scope = _engine(check, ANALYST).scope(USER, "sales")

    assert scope.reaches_datasource()
    assert scope.allows("reporting", "daily")
    assert not scope.allows("public", "orders")
    assert not scope.allows("reporting", "salaries")
    assert "FixedCheck" in scope.rule_for("public", "orders")
    assert "sales.reporting.salaries" in scope.rule_for("reporting", "salaries")

    # A check that reaches nothing in a datasource takes the datasource away.
    assert not _engine(FixedCheck(allow=["support"]), ANALYST).scope(USER, "sales").reaches_datasource()


def test_check_exception_denies_and_logs(caplog):
    engine = _engine(BrokenCheck(), ANALYST)

    with caplog.at_level(logging.WARNING, logger="nl2sql"):
        scope = engine.scope(USER, "sales")

    assert not scope.reaches_datasource()
    assert not scope.allows("public", "orders")
    assert "BrokenCheck" in scope.rule_for("public", "orders")
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "BrokenCheck" in logged and "sales" in logged and "TimeoutError" in logged


def test_check_is_called_once_per_datasource_per_run():
    check = FixedCheck(allow=["*"])
    engine = _engine(check, ANALYST)

    with engine.run():
        for _ in range(3):
            engine.scope(USER, "sales").allows("public", "orders")
            engine.scope(USER, "support")
    assert sorted(check.asked) == [("ana", "sales"), ("ana", "support")]

    # No cache across runs: the next run asks again.
    with engine.run():
        engine.scope(USER, "sales")
    assert check.asked.count(("ana", "sales")) == 2


def test_allow_all_with_a_check_uses_the_check():
    check = FixedCheck(allow=["sales.public.*"])
    engine = _engine(check, allow_all=True)

    scope = engine.scope(UserContext(user_id="ana"), "sales")
    assert scope.allows("public", "orders")
    assert not scope.allows("audit", "log")
    assert not engine.scope(UserContext(user_id="ana"), "support").reaches_datasource()


# --- the edges ---------------------------------------------------------------------


def test_an_invalid_pattern_in_a_grant_denies_like_an_exception(caplog):
    engine = _engine(FixedCheck(allow=["sales.orders"]), ANALYST)

    with caplog.at_level(logging.WARNING, logger="nl2sql"):
        scope = engine.scope(USER, "sales")

    assert not scope.allows("public", "orders")
    assert "two parts" in " ".join(r.getMessage() for r in caplog.records)


def test_a_check_that_returns_no_grant_denies():
    class NoGrant(FixedCheck):
        def grant(self, user, datasource_id):
            return {"allow": ["*"]}

    assert not _engine(NoGrant(), ANALYST).scope(USER, "sales").reaches_datasource()


def test_without_a_check_the_roles_decide_alone():
    scope = _engine(None, ANALYST).scope(USER, "sales")
    assert scope.allows("public", "orders")


def test_the_check_is_asked_about_an_anonymous_caller_too():
    check = FixedCheck(allow=["*"])
    _engine(check, allow_all=True).scope(None, "sales")
    assert check.asked == [(None, "sales")]


def test_a_fixed_check_satisfies_the_protocol():
    assert isinstance(FixedCheck(), PolicyCheck)


# --- the config ----------------------------------------------------------------------


def test_the_check_block_takes_a_type_and_its_own_keys():
    policies = Policies(allow_all=True, check={"type": "corp_iam", "url": "${env:IAM_URL}"})

    assert policies.check.type == "corp_iam"
    assert policies.check.model_extra == {"url": "${env:IAM_URL}"}


@pytest.mark.parametrize("value", ["corp_iam", ["corp_iam"], object()])
def test_the_check_must_be_a_block_or_a_check_object(value):
    with pytest.raises(ValidationError, match="check takes a mapping with a 'type' key"):
        Policies(allow_all=True, check=value)


def test_a_check_block_is_built_through_the_registry(tmp_path):
    from nl2sql.runtime import Runtime

    registry = Registry.default()
    registry.add_policy_check("fixed", FixedCheck)
    config = make_config(tmp_path, policies={
        "roles": {"analyst": {"description": "a", "allow": ["sales"]}},
        "check": {"type": "fixed", "allow": ["sales.public.*"]}})

    runtime = Runtime.components(config, registry)
    try:
        scope = runtime.policy_engine.scope(USER, "sales")
        assert scope.allows("public", "orders")
        assert not scope.allows("audit", "log")
    finally:
        runtime.close()


def test_an_unknown_check_is_a_config_problem(tmp_path):
    from nl2sql.config import ConfigError
    from nl2sql.runtime import Runtime

    config = make_config(tmp_path, policies={"allow_all": True, "check": {"type": "corp_iam"}})

    with pytest.raises(ConfigError) as caught:
        Runtime.components(config, Registry.default())
    assert "policies.check" in str(caught.value)
    assert "Unknown policy check 'corp_iam'" in str(caught.value)


def test_a_bad_key_in_the_check_block_is_a_config_problem(tmp_path):
    from nl2sql.config import ConfigError
    from nl2sql.runtime import Runtime

    registry = Registry.default()
    registry.add_policy_check("fixed", FixedCheck)
    config = make_config(tmp_path, policies={"allow_all": True, "check": {"type": "fixed", "alow": ["*"]}})

    with pytest.raises(ConfigError, match="alow"):
        Runtime.components(config, registry)


def test_a_check_of_another_interface_version_is_refused():
    class Old(FixedCheck):
        interface_version = 0

    registry = Registry.empty()
    registry.add_policy_check("old", Old)
    with pytest.raises(RegistryError, match="policy check interface 0; this engine needs 1"):
        registry.load("policy_check", "old")
