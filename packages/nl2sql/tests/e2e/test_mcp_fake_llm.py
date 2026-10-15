"""The MCP server over the demo project and the real graph, on the fake LLM (wave_a.md, section 12).

An in-process MCP client calls the tools; the engine is the demo's, indexed
locally, and every model answer comes from ``FakeLLMServer``.
"""
from __future__ import annotations

import pytest

pytest.importorskip("mcp")
import anyio  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from nl2sql import NL2SQL, UserContext  # noqa: E402
from nl2sql.mcp.server import build_server  # noqa: E402
from nl2sql.testing.fake_llm import FakeLLMServer  # noqa: E402

from .conftest import fake_llm_section, private_storage, project_config  # noqa: E402
from .recordings_chinook import RULES_ALBUMS_PER_ARTIST  # noqa: E402


@pytest.fixture
def engine_on(demo_project, tmp_path, monkeypatch):
    """``engine_on(rules)``: the demo engine, on a private copy of its index and state, answered from ``rules``."""
    opened = []

    def _make(rules):
        server = FakeLLMServer(rules).start()
        config = project_config(demo_project, "mcp.yaml", llm=fake_llm_section(server.base_url),
                                storage=private_storage(demo_project, tmp_path / "store"))
        monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "fake-mcp-test")
        engine = NL2SQL.from_config(config)
        opened.append((server, engine))
        return engine

    yield _make
    for server, engine in opened:
        engine.close()
        server.stop()


def _calls(engine, role, *calls):
    """Each ``(tool, arguments)`` in ``calls``, through one client session, as ``role``."""
    async def go():
        server = build_server(engine, UserContext(roles=[role]))
        async with create_connected_server_and_client_session(server) as client:
            results = [await client.call_tool(tool, arguments) for tool, arguments in calls]
        assert not any(r.isError for r in results), [r.content for r in results]
        return [r.structuredContent for r in results]

    return anyio.run(go)


@pytest.mark.e2e
def test_ask_returns_answer_sql_and_capped_rows(engine_on):
    [body] = _calls(engine_on(RULES_ALBUMS_PER_ARTIST), "admin",
                    ("ask", {"question": "How many albums does each artist have?"}))

    assert body["status"] == "success", body
    assert body["answer"]["summary"] == "Album counts per artist."
    [sq] = body["sub_queries"]
    assert "COUNT(" in sq["sql"] and sq["datasource_id"] == "chinook"
    assert sq["columns"] == ["artist_name", "album_count"]
    # Chinook has 204 artists with an album: the preview is the first 20.
    assert len(sq["rows"]) == 20 and sq["total_rows"] > 20 and sq["truncated"] is True
    assert body["trace_id"] and "errors" not in body


@pytest.mark.e2e
def test_a_viewer_sees_only_what_the_viewer_may_read(engine_on):
    """The demo's viewer reads the music catalog, not customers, and no support data at all."""
    listed, album, customer, ticket = _calls(
        engine_on(RULES_ALBUMS_PER_ARTIST), "viewer",
        ("list_datasources", {}),
        ("describe_table", {"datasource_id": "chinook", "table": "Album"}),
        ("describe_table", {"datasource_id": "chinook", "table": "Customer"}),
        ("describe_table", {"datasource_id": "support", "table": "tickets"}))

    assert "support" not in [d["id"] for d in listed["datasources"]]
    assert "chinook" in [d["id"] for d in listed["datasources"]]
    assert album["status"] == "ok" and album["table"]["name"] == "Album"
    assert customer["status"] == "not_found" and "Customer" not in customer["tables"]
    assert ticket["status"] == "not_found" and ticket["tables"] == []
