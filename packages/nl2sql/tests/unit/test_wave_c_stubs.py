"""Wave C's stubs (docs/architecture/wave_c.md, row C0b): the namespaces, the chat-tool entry points. (The exports are held by test_public_surface.)

Each stub has its final signature and raises ``NotImplementedError`` naming the row that fills it, so the
parallel PRs of the tracks touch different files. Nothing here runs a conversation, a chart or a dashboard.
"""
from __future__ import annotations

import inspect

import pytest
from pydantic import BaseModel

from nl2sql import NL2SQL, Registry, UserContext
from nl2sql.chat_tools import ChatTool, ToolContext
from nl2sql.conversations.models import Conversation
from nl2sql.testing.config import make_config

ADMIN = UserContext(user_id="root", roles=["admin"])

# namespace -> method -> the row that fills it. Every public method of a stub class is listed.
STUBS = {
    "conversations": {"start": "C1", "ask": "C1", "stream": "C1", "retry": "C1", "ask_only": "C1", "get": "C1",
                      "list": "C1", "delete": "C1", "tool": "C2", "report": "C2"},
    "chat_tools": {"list": "C2"},
    "workspaces": {"create": "D1", "rename": "D1", "list": "D1", "delete": "D1"},
    "dashboards": {"get": "D1", "list": "D1", "delete": "D1", "pin": "D1", "move": "D1", "edit": "D1",
                   "rename": "D1", "rename_tile": "D1", "set_tile_chart": "D1", "remove_tile": "D1",
                   "arrange": "D1", "set_filters": "D1", "set_schedule": "D1",
                   "open": "D2", "refresh": "D2", "filter_values": "D2", "run_scheduled": "D2",
                   "generate": "D3", "suggest_questions": "D3"},
    "api_keys": {"create": "M1", "list": "M1", "revoke": "M1", "verify": "M1"},
}
# Methods added to namespaces that already exist.
ADDED = {("datasources", "scan"): "L1", ("datasources", "diagram"): "K2", ("datasources", "save"): "L2",
         ("results", "chart"): "K1"}


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    built = NL2SQL(make_config(tmp_path_factory.mktemp("engine")))
    yield built
    built.close()


def _dummy(parameter: inspect.Parameter):
    if parameter.name in {"user_context", "by"}:
        return ADMIN
    return "x"


def _call(namespace, method):
    function = getattr(namespace, method)
    required = {p.name: _dummy(p) for p in inspect.signature(function).parameters.values()
                if p.default is p.empty and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
    return function(**required)


def test_stub_namespaces_name_their_pr(engine):
    for name, methods in STUBS.items():
        namespace = getattr(engine, name)
        public = {m for m in dir(namespace) if not m.startswith("_") and callable(getattr(namespace, m))}
        assert public == set(methods), f"engine.{name} has methods the test does not list (or lacks some)"
        for method, row in methods.items():
            with pytest.raises(NotImplementedError, match=rf"engine\.{name}\.{method} arrives with {row}\b"):
                _call(namespace, method)
    for (name, method), row in ADDED.items():
        with pytest.raises(NotImplementedError, match=rf"engine\.{name}\.{method} arrives with {row}\b"):
            _call(getattr(engine, name), method)


def test_a_stub_stream_fails_at_the_call_not_on_the_first_next(engine):
    with pytest.raises(NotImplementedError):
        engine.conversations.stream("c", "hello", user_context=ADMIN)


def test_the_namespaces_are_built_once_and_import_nl2sql_stays_light(engine):
    for name in STUBS:
        assert getattr(engine, name) is getattr(engine, name)


def test_stubs_keep_the_signatures_the_design_gives(engine):
    ask = inspect.signature(engine.conversations.ask).parameters
    assert list(ask) == ["conversation_id", "text", "user_context", "choice", "execute", "cancellation_token"]
    assert ask["user_context"].kind is inspect.Parameter.KEYWORD_ONLY and ask["text"].default is None
    refresh = inspect.signature(engine.dashboards.refresh).parameters
    assert list(refresh) == ["dashboard_id", "user_context", "tile_id", "filters", "approvals"]
    assert list(inspect.signature(engine.dashboards.run_scheduled).parameters) == ["now"]
    assert list(inspect.signature(engine.api_keys.create).parameters) == [
        "user_id", "roles", "by", "tenant_id", "label"]
    assert list(inspect.signature(engine.results.chart).parameters) == ["result", "result_id", "kind", "tenant_id"]
    scan = inspect.signature(engine.datasources.scan).parameters
    assert list(scan) == ["folder", "url", "ports"] and scan["ports"].default is True


# ---------------------------------------------------------------- chat-tool entry points

CHAT_TOOL_ROWS = {"chart": "K1", "diagram": "K2", "report": "C2", "dashboard_edit": "D4", "dashboard_pin": "D4"}


def test_the_five_chat_tool_entry_points_load_their_stub_modules():
    registry = Registry.default()
    assert registry.names("chat_tool") == sorted(CHAT_TOOL_ROWS)
    context = ToolContext(conversation=Conversation.model_construct(), user=ADMIN, engine=None)
    for name, row in CHAT_TOOL_ROWS.items():
        cls = registry.load("chat_tool", name)
        assert cls.interface_version == 1 and cls.description and issubclass(cls.args_model, BaseModel)
        assert cls.__module__ == f"nl2sql.chat_tools.{name}"
        tool = cls()
        assert isinstance(tool, ChatTool)
        with pytest.raises(NotImplementedError, match=rf"The {name} chat tool arrives with {row}\b"):
            tool(context, cls.args_model())
