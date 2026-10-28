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
    "conversations": {"tool": "C2", "report": "C2"},
    "chat_tools": {"list": "C2"},
    "workspaces": {},
    "dashboards": {"open": "D2", "refresh": "D2", "filter_values": "D2", "run_scheduled": "D2",
                   "generate": "D3", "suggest_questions": "D3"},
}
# Methods a row has filled since: they are real now and have their own tests.
FILLED = {"conversations": {"start", "ask", "stream", "retry", "ask_only", "get", "list", "delete"},
          "workspaces": {"create", "rename", "list", "delete"},
          "dashboards": {"get", "list", "delete", "pin", "move", "edit", "rename", "rename_tile", "set_tile_chart",
                         "remove_tile", "arrange", "set_filters", "set_schedule"}}
# Methods added to namespaces that already exist.
ADDED = {("datasources", "diagram"): "K2", ("datasources", "save"): "L2"}


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
        assert public - FILLED.get(name, set()) == set(methods), f"engine.{name} has methods the test does not list (or lacks some)"
        for method, row in methods.items():
            with pytest.raises(NotImplementedError, match=rf"engine\.{name}\.{method} arrives with {row}\b"):
                _call(namespace, method)
    for (name, method), row in ADDED.items():
        with pytest.raises(NotImplementedError, match=rf"engine\.{name}\.{method} arrives with {row}\b"):
            _call(getattr(engine, name), method)


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


# ---------------------------------------------------------------- chat-tool entry points

CHAT_TOOL_ROWS = {"diagram": "K2", "report": "C2", "dashboard_edit": "D4", "dashboard_pin": "D4"}
FILLED_CHAT_TOOLS = ["chart"]  # K1 filled it; a row's PR moves its tool out of CHAT_TOOL_ROWS into this list


def test_the_five_chat_tool_entry_points_load_their_stub_modules():
    registry = Registry.default()
    assert registry.names("chat_tool") == sorted([*CHAT_TOOL_ROWS, *FILLED_CHAT_TOOLS])
    context = ToolContext(conversation=Conversation.model_construct(), user=ADMIN, engine=None)
    for name, row in CHAT_TOOL_ROWS.items():
        cls = registry.load("chat_tool", name)
        assert cls.interface_version == 1 and cls.description and issubclass(cls.args_model, BaseModel)
        assert cls.__module__ == f"nl2sql.chat_tools.{name}"
        tool = cls()
        assert isinstance(tool, ChatTool)
        with pytest.raises(NotImplementedError, match=rf"The {name} chat tool arrives with {row}\b"):
            tool(context, cls.args_model())
