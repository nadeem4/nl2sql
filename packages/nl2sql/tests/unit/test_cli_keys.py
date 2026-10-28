"""``nl2sql keys create``, ``list`` and ``revoke`` (docs/architecture/wave_c.md, section 9, row M1)."""
from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from nl2sql import NL2SQL
from nl2sql.cli.main import app
from nl2sql.testing.config import make_config

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
ROLES = {"admin": {"description": "Everything", "allow": ["*"]},
         "analyst": {"description": "Reads", "allow": ["*"]}}
SECTIONS = {"policies": {"roles": ROLES, "admin_roles": ["admin"]}}


@pytest.fixture
def project(tmp_path):
    make_config(tmp_path, **SECTIONS)
    return tmp_path


def _run(project, *args):
    result = runner.invoke(app, ["--config", str(project / "nl2sql.yaml"), "keys", *args])
    return result, re.sub(r"\s+", " ", ANSI.sub("", result.stdout))


def _token(out: str) -> str:
    return re.search(r"nl2sql_[0-9a-f]{8}_[A-Za-z0-9_-]{43}", out).group(0)


def test_create_prints_the_token_once_and_it_verifies(project):
    result, out = _run(project, "create", "--as", "admin", "--user", "ana", "--role", "analyst",
                       "--tenant", "acme", "--label", "laptop")

    assert result.exit_code == 0, out
    token = _token(out)
    assert out.count(token) == 1
    with NL2SQL(make_config(project, **SECTIONS)) as engine:
        who = engine.api_keys.verify(token)
    assert (who.user_id, who.tenant_id, who.roles) == ("ana", "acme", ["analyst"])


def test_list_shows_keys_never_the_token(project):
    _, created = _run(project, "create", "--as", "admin", "--user", "ana", "--role", "analyst", "--label", "laptop")
    token = _token(created)

    result, out = _run(project, "list", "--as", "admin")

    assert result.exit_code == 0, out
    assert token.split("_")[1] in out and "ana" in out and "laptop" in out and "analyst" in out
    assert token not in out and token.split("_", 2)[2] not in out


def test_list_with_no_keys_says_so(project):
    result, out = _run(project, "list", "--as", "admin")

    assert result.exit_code == 0 and "No API keys" in out


def test_revoke_stops_the_key_verifying(project):
    _, created = _run(project, "create", "--as", "admin", "--user", "ana", "--role", "analyst")
    token = _token(created)

    result, out = _run(project, "revoke", token.split("_")[1], "--as", "admin")

    assert result.exit_code == 0 and "revoked" in out
    with NL2SQL(make_config(project, **SECTIONS)) as engine:
        assert engine.api_keys.verify(token) is None
    _, listed = _run(project, "list", "--as", "admin")
    assert "revoked" in listed


def test_a_non_admin_is_refused_with_exit_1(project):
    result, out = _run(project, "create", "--as", "analyst", "--user", "ana", "--role", "analyst")

    assert result.exit_code == 1 and "admin" in out and "nl2sql_" not in out


def test_an_unconfigured_key_role_is_refused(project):
    result, out = _run(project, "create", "--as", "admin", "--user", "ana", "--role", "ghost")

    assert result.exit_code == 1 and "ghost" in out


def test_an_unknown_id_is_refused(project):
    result, out = _run(project, "revoke", "deadbeef", "--as", "admin")

    assert result.exit_code == 1 and "No API key" in out


def test_under_allow_all_keys_are_managed_without_as(tmp_path):
    make_config(tmp_path)  # allow_all, no roles

    result, out = _run(tmp_path, "create", "--user", "ana")
    assert result.exit_code == 0, out
    token = _token(out)

    result, out = _run(tmp_path, "list")
    assert result.exit_code == 0 and token.split("_")[1] in out and "ana" in out

    result, out = _run(tmp_path, "revoke", token.split("_")[1])
    assert result.exit_code == 0 and "revoked" in out
    with NL2SQL(make_config(tmp_path)) as engine:
        assert engine.api_keys.verify(token) is None


def test_under_allow_all_a_key_role_is_refused(tmp_path):
    make_config(tmp_path)

    result, out = _run(tmp_path, "create", "--user", "ana", "--role", "admin")

    assert result.exit_code == 1 and "admin" in out and "nl2sql_" not in out


def test_an_unknown_as_role_is_refused(project):
    result, out = _run(project, "list", "--as", "ghost")

    assert result.exit_code == 1 and "ghost" in out
