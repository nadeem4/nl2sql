"""The namespaces' signatures and the chat-tool entry points. (The exports are held by test_public_surface.)"""
from __future__ import annotations

import inspect

import pytest

from nl2sql import NL2SQL, Registry, UserContext
from nl2sql.testing.config import make_config

ADMIN = UserContext(user_id="root", roles=["admin"])


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    built = NL2SQL(make_config(tmp_path_factory.mktemp("engine")))
    yield built
    built.close()


def test_the_namespaces_are_built_once(engine):
    for name in ("workspaces", "dashboards"):
        assert getattr(engine, name) is getattr(engine, name)


def test_the_namespaces_keep_their_signatures(engine):
    ask = inspect.signature(engine.conversations.ask).parameters
    assert list(ask) == ["conversation_id", "text", "user_context", "choice", "execute", "cancellation_token",
                        "llm_override"]
    assert ask["user_context"].kind is inspect.Parameter.KEYWORD_ONLY and ask["text"].default is None
    refresh = inspect.signature(engine.dashboards.refresh).parameters
    assert list(refresh) == ["dashboard_id", "user_context", "tile_id", "filters", "approvals"]
    assert list(inspect.signature(engine.dashboards.run_scheduled).parameters) == ["now"]
    assert list(inspect.signature(engine.api_keys.create).parameters) == [
        "user_id", "roles", "by", "tenant_id", "label"]


def test_the_chat_tool_entry_points_are_registered():
    registry = Registry.default()
    assert registry.names("chat_tool") == sorted(["chart", "diagram", "report", "dashboard_edit", "dashboard_pin"])
