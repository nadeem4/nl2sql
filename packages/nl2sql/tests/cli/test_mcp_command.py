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
    """A project with one role and no default role (or ``policies`` as given), named by NL2SQL_CONFIG."""
    def write(**policies):
        monkeypatch.setenv("NL2SQL_CONFIG", str(tmp_path / "nl2sql.yaml"))
        make_config(tmp_path, policies=policies if "allow_all" in policies else {"roles": ROLES, **policies})
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


def test_mcp_under_allow_all_starts_without_a_role_and_warns(project, served):
    project(allow_all=True)

    result = runner.invoke(app, ["mcp"])

    assert result.exit_code == 0, result.output
    assert served[0].roles == []
    assert "WARNING" in result.stderr and "allow_all" in result.stderr
    assert result.stdout == ""


def test_mcp_under_allow_all_refuses_an_unknown_role(project, served):
    project(allow_all=True)

    result = runner.invoke(app, ["mcp", "--role", "analyst"])

    assert result.exit_code == 1
    assert "analyst" in result.stderr and served == []


def test_mcp_refuses_a_default_role_that_names_no_role(project, served):
    with pytest.raises(Exception, match="(?i)default_role 'ghost' is not one of the roles"):
        project(default_role="ghost")

    result = runner.invoke(app, ["mcp"])  # the file was written; loading it is what fails

    assert result.exit_code == 1
    assert "ghost" in result.stderr and served == []


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


# ------------------------------------------------------------------ --http (docs/architecture/wave_c.md, section 9)

@pytest.fixture
def served_http(monkeypatch):
    """Stands in for the HTTP server: records ``(identity, host, port)`` and serves nothing."""
    from nl2sql.mcp import http

    seen = []
    monkeypatch.setattr(http, "_run", lambda application, host, port: seen.append((host, port)))
    real = http.build_app
    monkeypatch.setattr(http, "build_app", lambda engine, identity: seen.append(identity) or real(engine, identity))
    return seen


def _http_project(tmp_path, monkeypatch, mcp=None, **policies):
    monkeypatch.setenv("NL2SQL_CONFIG", str(tmp_path / "nl2sql.yaml"))
    make_config(tmp_path, policies={"roles": ROLES, **policies}, **({"mcp": mcp} if mcp else {}))


def test_stdio_is_still_the_default(project, served, monkeypatch):
    from nl2sql.mcp import http

    monkeypatch.setattr(http, "serve", lambda *a, **k: pytest.fail("HTTP is opt-in"))
    project()

    result = runner.invoke(app, ["mcp", "--role", "analyst"])

    assert result.exit_code == 0, result.output
    assert served[1] == "stdio"


def test_http_with_api_keys_needs_no_role_and_serves_the_configured_address(tmp_path, monkeypatch, served_http):
    _http_project(tmp_path, monkeypatch, {"http": {"port": 9123}})

    result = runner.invoke(app, ["mcp", "--http"])

    assert result.exit_code == 0, result.output
    assert served_http == [None, ("127.0.0.1", 9123)]


def test_http_host_and_port_flags_override_the_config(tmp_path, monkeypatch, served_http):
    _http_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["mcp", "--http", "--host", "0.0.0.0", "--port", "9000"])

    assert result.exit_code == 0, result.output
    assert served_http[1] == ("0.0.0.0", 9000)


def test_http_with_api_keys_refuses_a_role_or_user(tmp_path, monkeypatch, served_http):
    _http_project(tmp_path, monkeypatch)

    result = runner.invoke(app, ["mcp", "--http", "--role", "analyst"])

    assert result.exit_code == 1 and "key" in result.stderr and served_http == []


def test_http_with_auth_none_serves_as_the_role_given(tmp_path, monkeypatch, served_http):
    _http_project(tmp_path, monkeypatch, {"auth": "none"})

    result = runner.invoke(app, ["mcp", "--http", "--role", "analyst", "--user", "ana"])

    assert result.exit_code == 0, result.output
    assert served_http[0].roles == ["analyst"] and served_http[0].user_id == "ana"


def test_http_with_auth_none_still_needs_a_role(tmp_path, monkeypatch, served_http):
    _http_project(tmp_path, monkeypatch, {"auth": "none"})

    result = runner.invoke(app, ["mcp", "--http"])

    assert result.exit_code == 1 and "--role" in result.stderr and served_http == []


def test_public_bind_without_keys_refuses_to_start(tmp_path, monkeypatch, served_http):
    _http_project(tmp_path, monkeypatch, {"auth": "none"})

    result = runner.invoke(app, ["mcp", "--http", "--role", "analyst", "--host", "0.0.0.0"])

    assert result.exit_code == 1
    assert "auth: api_keys" in result.stderr and "0.0.0.0" in result.stderr
    assert served_http[1:] == [], "nothing was served"
