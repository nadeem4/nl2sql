"""``NL2SQL.serve_mcp``: the facade method ``nl2sql mcp`` calls to start the MCP server (D232)."""
import pytest

pytest.importorskip("mcp")

import nl2sql
from nl2sql import UserContext
from nl2sql.mcp import http as http_server
from nl2sql.mcp import server


def test_serve_mcp_serves_over_stdio_by_default(monkeypatch):
    calls = []

    class Built:
        def run(self, transport):
            calls.append(("run", transport))

    monkeypatch.setattr(server, "build_server", lambda engine, identity: calls.append((engine, identity)) or Built())
    monkeypatch.setattr(http_server, "serve", lambda *a, **k: pytest.fail("HTTP is opt-in"))
    engine, identity = object(), UserContext(user_id="ana", roles=["analyst"])

    nl2sql.NL2SQL.serve_mcp(engine, identity)

    assert calls == [(engine, identity), ("run", "stdio")]


def test_serve_mcp_over_http_passes_the_identity_host_and_port(monkeypatch):
    seen = []
    monkeypatch.setattr(server, "build_server", lambda *a, **k: pytest.fail("stdio is the default"))
    monkeypatch.setattr(http_server, "serve",
                        lambda engine, identity, *, host=None, port=None: seen.append((engine, identity, host, port)))
    engine = object()

    nl2sql.NL2SQL.serve_mcp(engine, None, http=True, host="0.0.0.0", port=9123)

    assert seen == [(engine, None, "0.0.0.0", 9123)]


def test_serve_mcp_over_stdio_needs_an_identity(monkeypatch):
    monkeypatch.setattr(server, "build_server", lambda *a, **k: pytest.fail("refused before the server is built"))

    with pytest.raises(ValueError, match="UserContext"):
        nl2sql.NL2SQL.serve_mcp(object(), None)
