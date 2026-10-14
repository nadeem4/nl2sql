"""The ``realdb`` fixtures skip loudly with the fix, or fail under ``REALDB_REQUIRED=1``.

docs/architecture/phase2.md, section 3: a green run that skipped the real
databases proves nothing, so CI sets ``REALDB_REQUIRED=1``.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SERVERS_PY = Path(__file__).resolve().parents[1] / "realdb" / "servers.py"


@pytest.fixture(scope="module")
def servers():
    spec = importlib.util.spec_from_file_location("realdb_servers_under_test", SERVERS_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up there
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def test_realdb_fixture_skips_with_the_fix_when_unset(servers):
    with pytest.raises(pytest.skip.Exception) as skipped:
        servers.require("postgres", {})
    message = str(skipped.value)
    assert "realdb: no PostgreSQL" in message
    assert "docker compose -f testbed/docker-compose.yml up -d --wait" in message
    assert "REALDB=compose" in message


def test_realdb_fixture_fails_when_required(servers):
    with pytest.raises(pytest.fail.Exception, match="realdb: no SQL Server"):
        servers.require("mssql", {"REALDB_REQUIRED": "1"})


def test_compose_points_at_the_published_ports(servers):
    found = {name: servers.require(name, {"REALDB": "compose"}) for name in servers.SERVERS}
    assert {name: (s.url.host, s.url.port) for name, s in found.items()} == {
        "postgres": ("127.0.0.1", 15432), "mysql": ("127.0.0.1", 13306), "mssql": ("127.0.0.1", 11433)}


def test_a_server_url_wins_over_compose(servers):
    environ = {"REALDB": "compose", "REALDB_MYSQL": "mysql://root:p%40ss@10.0.0.5:3306/"}
    server = servers.require("mysql", environ)
    assert (server.url.host, server.url.port, server.url.password) == ("10.0.0.5", 3306, "p@ss")
    source = servers.datasource(server)
    assert source.type == "mysql"
    assert source.connection["database"] == "sakila" and source.connection["password"] == "p@ss"
