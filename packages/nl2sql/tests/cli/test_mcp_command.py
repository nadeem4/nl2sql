"""``nl2sql mcp``: the engine over stdio, as the role the command line names (wave_a.md, D33)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from nl2sql.cli.main import app
from nl2sql.testing.config import make_config

pytest.importorskip("mcp")
import anyio  # noqa: E402
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

runner = CliRunner()
ROLES = {"analyst": {"description": "Reads everything", "allow": ["*"]}}


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project with one role and no default role, named by NL2SQL_CONFIG."""
    def write(**policies):
        make_config(tmp_path, policies={"roles": ROLES, **policies})
        monkeypatch.setenv("NL2SQL_CONFIG", str(tmp_path / "nl2sql.yaml"))
        return tmp_path / "nl2sql.yaml"

    return write


@pytest.fixture
def served(monkeypatch):
    """Stands in for the server: records the identity ``nl2sql mcp`` serves as, and serves nothing."""
    import nl2sql.mcp.server as server

    seen = []

    class _Server:
        def run(self, transport):
            seen.append(transport)

    def build(engine, identity):
        seen.append(identity)
        return _Server()

    monkeypatch.setattr(server, "build_server", build)
    return seen


def test_mcp_without_a_role_refuses_to_start(project, served):
    project()

    result = runner.invoke(app, ["mcp"])

    assert result.exit_code == 1
    assert "--role" in result.stderr and "policies.default_role" in result.stderr
    assert result.stdout == "" and served == []


def test_mcp_with_an_unknown_role_refuses_to_start(project, served):
    project()

    result = runner.invoke(app, ["mcp", "--role", "anlyst"])

    assert result.exit_code == 1
    assert "anlyst" in result.stderr and "analyst" in result.stderr
    assert served == []


def test_mcp_serves_as_the_roles_and_user_given(project, served):
    project()

    result = runner.invoke(app, ["mcp", "--role", "analyst", "--user", "ana"])

    assert result.exit_code == 0, result.output
    identity, transport = served
    assert identity.roles == ["analyst"] and identity.user_id == "ana"
    assert transport == "stdio"


def test_mcp_without_a_role_serves_as_the_default_role(project, served):
    project(default_role="analyst")

    result = runner.invoke(app, ["mcp"])

    assert result.exit_code == 0, result.output
    assert served[0].roles == ["analyst"]


def test_logs_go_to_stderr(project):
    """stdout is the protocol: a real ``nl2sql mcp`` writes nothing else there, and logs to stderr."""
    config = project()

    done = subprocess.run([sys.executable, "-m", "nl2sql.cli.main", "--config", str(config), "mcp",
                           "--role", "analyst"], input="", capture_output=True, text=True, encoding="utf-8",
                          timeout=180)

    assert done.returncode == 0, done.stderr
    assert done.stdout == ""
    assert "Serving 0 datasource(s) over stdio as role(s) analyst" in done.stderr


def test_a_client_talks_to_nl2sql_mcp_over_stdio(project, tmp_path):
    """The real process and the SDK's stdio client: a handshake and a tool call."""
    config = project()
    params = StdioServerParameters(command=sys.executable, env=dict(os.environ),
                                   args=["-m", "nl2sql.cli.main", "--config", str(config), "mcp",
                                         "--role", "analyst"])

    async def go():
        with open(tmp_path / "stderr.log", "w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return (await session.call_tool("list_datasources", {})).structuredContent

    assert anyio.run(go) == {"datasources": []}
