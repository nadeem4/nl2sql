"""Wave C's shared shapes (docs/architecture/wave_c.md, row C0a): models, config, codes, events, registry kind.

Nothing here runs a conversation, a chart or a dashboard: those arrive in later PRs. These tests hold the
names and the strictness every later PR builds on.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar, get_args

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from nl2sql import ConfigError, Registry, load_config
from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.config import Conversations, Dashboards, Mcp, Settings
from nl2sql.config.models import AgentName
from nl2sql.registry import GROUPS, INTERFACE_VERSIONS, RegistryError
from nl2sql.testing.config import make_config

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- config

def test_new_config_sections_have_the_designed_defaults(tmp_path):
    config = make_config(tmp_path)
    c, d, m = config.conversations, config.dashboards, config.mcp
    assert (c.context_turns, c.max_tool_calls, c.max_turns, c.max_age_days) == (3, 12, 50, 30)
    assert (d.max_tiles, d.min_refresh_minutes, d.schedule_identity_max_age_days) == (10, 15, 30)
    assert (m.http.host, m.http.port, m.http.path) == ("127.0.0.1", 8765, "/mcp")
    assert m.http.allowed_hosts == [
        "127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1", "localhost", "[::1]"] and m.http.allowed_origins == []
    assert m.auth == "api_keys"
    assert config.chat_tools is None, "absent: the five built-ins"
    charts = config.settings.charts
    assert (charts.max_points, charts.max_categories, charts.max_series) == (1000, 20, 5)
    assert Conversations(context_turns=5).context_turns == 5
    assert Conversations(max_age_days=None).max_age_days is None, "null keeps conversations"


@pytest.mark.parametrize("section, body, wrong", [
    ("conversations", {"context_turns": 3}, "context_turn"),
    ("dashboards", {"max_tiles": 10}, "max_tile"),
    ("mcp", {"auth": "api_keys"}, "auths"),
])
def test_new_config_sections_are_strict(tmp_path, section, body, wrong):
    with pytest.raises(ConfigError) as caught:
        make_config(tmp_path, **{section: {wrong: 1}})
    assert "Unknown key" in str(caught.value) and next(iter(body)) in str(caught.value), "did you mean"


def test_nested_new_config_sections_are_strict_too(tmp_path):
    with pytest.raises(ConfigError, match="Unknown key"):
        make_config(tmp_path, mcp={"http": {"hosts": "x"}})
    with pytest.raises(ConfigError, match="Unknown key"):
        make_config(tmp_path, settings={"charts": {"max_point": 1}})


@pytest.mark.parametrize("section", [
    {"conversations": {"context_turns": 0}},
    {"conversations": {"max_tool_calls": 0}},
    {"conversations": {"max_age_days": 0}},
    {"dashboards": {"min_refresh_minutes": 0}},
    {"mcp": {"auth": "basic"}},
    {"mcp": {"http": {"port": 70000}}},
    {"mcp": {"http": {"path": "mcp"}}},
    {"settings": {"charts": {"max_series": 0}}},
    {"chat_tools": "chart"},
    {"chat_tools": ["chart", "chart"]},
    {"chat_tools": [{"name": "chart"}]},
])
def test_new_config_values_are_checked(tmp_path, section):
    with pytest.raises(ConfigError):
        make_config(tmp_path, **section)


def test_new_sections_can_be_included_from_their_own_file(tmp_path):
    (tmp_path / "conversations.yaml").write_text("context_turns: 5\nmax_age_days: null\n", encoding="utf-8")
    (tmp_path / "chat_tools.yaml").write_text("- chart\n- report\n", encoding="utf-8")
    (tmp_path / "dashboards.yaml").write_text("max_tiles: 6\n", encoding="utf-8")
    (tmp_path / "mcp.yaml").write_text("http: {port: 9000}\nauth: none\n", encoding="utf-8")
    config = make_config(tmp_path, conversations={"file": "conversations.yaml"},
                         chat_tools={"file": "chat_tools.yaml"}, dashboards={"file": "dashboards.yaml"},
                         mcp={"file": "mcp.yaml"})
    assert config.conversations.context_turns == 5 and config.conversations.max_age_days is None
    assert config.chat_tools == ["chart", "report"]
    assert config.dashboards.max_tiles == 6
    assert (config.mcp.http.port, config.mcp.auth) == (9000, "none")
    assert config.locate("chat_tools[0]") == ("chat_tools.yaml", "[0]")


def test_an_empty_chat_tools_section_means_no_tools(tmp_path):
    assert make_config(tmp_path, chat_tools=[]).chat_tools == []


def test_the_new_sections_load_from_a_file_on_disk(tmp_path):
    make_config(tmp_path, conversations={"context_turns": 4})
    assert load_config(tmp_path / "nl2sql.yaml").conversations.context_turns == 4


def test_conversation_is_an_agent(tmp_path):
    assert "conversation" in get_args(AgentName)
    assert "conversation_rewriter" not in get_args(AgentName), "the first draft's rewrite agent is gone (D101)"
    config = make_config(tmp_path, llm={"provider": "openai", "model": "gpt-test",
                                        "agents": {"conversation": {"model": "gpt-mini"}}})
    assert config.llm.agents["conversation"].model == "gpt-mini"
    with pytest.raises(ConfigError):
        make_config(tmp_path, llm={"provider": "openai", "model": "gpt-test",
                                   "agents": {"conversation_rewriter": {"model": "gpt-mini"}}})


# ---------------------------------------------------------------- the registry

def test_registry_lists_chat_tools():
    assert GROUPS["chat_tool"] == "nl2sql.chat_tools"
    assert INTERFACE_VERSIONS["chat_tool"] == 1
    registry = Registry.empty()
    tool = type("Tool", (), {"interface_version": 1})
    registry.add_chat_tool("chart", tool)
    registry.chat_tool("report")(tool)
    assert registry.names("chat_tool") == ["chart", "report"]
    assert registry.load("chat_tool", "chart") is tool
    with pytest.raises(RegistryError, match="already registered"):
        registry.add_chat_tool("chart", tool)
    registry.add_chat_tool("old", type("Old", (), {"interface_version": 2}))
    with pytest.raises(RegistryError, match="chat tool interface 2; this engine needs 1"):
        registry.load("chat_tool", "old")
    with pytest.raises(RegistryError, match="Unknown chat tool 'chrat'. Did you mean 'chart'"):
        registry.load("chat_tool", "chrat")


# ---------------------------------------------------------------- error codes and exceptions

NEVER_RETRIED = ["MODEL_STEP_INVALID", "CONVERSATION_TOO_LONG", "TILE_NEEDS_FIXING"]
OTHER_CODES = ["TOOL_FAILED", "TOOL_NOT_APPLICABLE", "UNKNOWN_CHAT_TOOL"]


@pytest.mark.parametrize("code", NEVER_RETRIED)
def test_wave_c_codes_that_end_a_step_are_never_retried(code):
    error = PipelineError(node="n", message="m", severity=ErrorSeverity.ERROR, error_code=ErrorCode(code))
    assert not error.is_retryable


@pytest.mark.parametrize("code", OTHER_CODES)
def test_the_other_wave_c_codes_exist(code):
    assert ErrorCode(code).value == code


def test_wave_c_exceptions_have_the_designed_bases():
    from nl2sql.common import errors

    for name in ("ConversationNotFound", "ConversationBusy", "DashboardNotFound", "WorkspaceNotFound"):
        assert issubclass(getattr(errors, name), LookupError), name
    for name in ("NotIndexed", "NoTiles", "WorkspaceNotEmpty", "ScheduleTooFrequent", "SecretValueInConfig"):
        assert issubclass(getattr(errors, name), ValueError), name
    assert str(errors.ConversationNotFound("c1")) == "c1"


# ---------------------------------------------------------------- events

WAVE_C_EVENTS = {  # name: is a run event
    "conversation.started": False, "conversation.step": True, "conversation.turn": True, "chat_tool.called": True,
    "dashboard.generated": False, "dashboard.refreshed": False, "dashboard.changed": False,
    "dashboard.schedule_set": False, "dashboard.schedule_stopped": False, "workspace.changed": False,
    "datasource.saved": False, "api_key.created": False, "api_key.revoked": False, "mcp.denied": False,
}
AUDITED = {"datasource.saved", "api_key.created", "api_key.revoked", "mcp.denied", "dashboard.schedule_set",
           "dashboard.schedule_stopped"}


def test_wave_c_events_are_catalogued():
    from nl2sql.events.catalog import AUDIT_EVENTS, CATALOG, STREAM_EVENTS

    for name, run in WAVE_C_EVENTS.items():
        assert CATALOG[name].run is run, name
        assert name not in STREAM_EVENTS, f"{name} is not on the run stream (conversations.stream adds its own, A4)"
    assert AUDITED <= AUDIT_EVENTS
    assert not ({"conversation.turn", "dashboard.changed", "workspace.changed"} & AUDIT_EVENTS)
    CATALOG["conversation.step"].model(conversation_id="c", turn=1, tool="ask", status="ok", trace_id="t",
                                       args=["question"])
    CATALOG["conversation.turn"].model(conversation_id="c", turn=1, kind="message", status="answered",
                                       trace_id="t", steps=1)
    CATALOG["dashboard.refreshed"].model(dashboard_id="d", trigger="schedule", counts={"ok": 2}, filters=["f1"])
    CATALOG["mcp.denied"].model(reason="revoked", key_id="k1234567")
    with pytest.raises(ValidationError):
        CATALOG["mcp.denied"].model(reason="revoked", token="nl2sql_x_y")
    with pytest.raises(ValidationError):
        CATALOG["conversation.step"].model(conversation_id="c", turn=1, tool="ask", status="ok", rows=[[1]])
    with pytest.raises(ValidationError):
        CATALOG["api_key.created"].model(key_id="k", user_id="u", roles=[], secret="s")


# ---------------------------------------------------------------- the result carries a decomposition and kinds

def test_result_summary_carries_column_kinds():
    from nl2sql.events.catalog import ResultSummaryData
    from nl2sql.execution.artifacts.results import ResultSummary

    summary = ResultSummary(node_id="n", columns=["d", "total"], row_count=2)
    assert summary.column_kinds == {}, "the field defaults to empty; the artifact writer fills it"
    kinds = {"d": "temporal", "total": "number"}
    assert ResultSummary(node_id="n", column_kinds=kinds).column_kinds == kinds
    with pytest.raises(ValidationError):
        ResultSummary(node_id="n", column_kinds={"d": "date"})
    assert ResultSummaryData(**summary.model_dump()).column_kinds == {}, "the event carries it too"


def _decomposition():
    from nl2sql.pipeline.nodes.decomposer.schemas import CombineGroup, CombineInput, DecomposerResponse, SubQuery

    sub_query = SubQuery(id="sq1", datasource_id="ds", intent="Count the rows.")
    return DecomposerResponse(sub_queries=[sub_query], combine_groups=[
        CombineGroup(group_id="g1", operation="standalone", inputs=[CombineInput(subquery_id="sq1")])])


def test_query_result_decomposition_is_set_on_a_run_that_has_one():
    from nl2sql.api.query_api import QueryResult, result_from_state

    decomposition = _decomposition()
    assert QueryResult().decomposition is None
    assert result_from_state({"decomposer_response": decomposition}).decomposition == decomposition
    from_dict = result_from_state({"decomposer_response": decomposition.model_dump(mode="json")})
    assert from_dict.decomposition == decomposition, "LangGraph may hand back a plain dict"
    assert result_from_state({}).decomposition is None
    assert QueryResult.model_validate_json(from_dict.model_dump_json()).decomposition == decomposition


def test_a_run_a_hook_ended_withholds_its_decomposition_with_its_data():
    from nl2sql.api.query_api import result_from_state
    from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError

    denied = PipelineError(node="hook", message="no", severity=ErrorSeverity.ERROR, error_code=ErrorCode.HOOK_DENIED)
    result = result_from_state({"decomposer_response": _decomposition(), "errors": [denied]})
    assert result.decomposition is None


# ---------------------------------------------------------------- the models

def _spec():
    from nl2sql.charts.spec import ChartData, ChartSpec, Encoding

    return ChartSpec(kind="bar", title="Revenue by genre", x=Encoding(field="genre", kind="text", title="Genre"),
                     y=[Encoding(field="total", kind="number", title="Total")],
                     data=ChartData(trace_id="t", result_id="n", columns=["genre", "total"], rows=[["Rock", 1]],
                                    truncated=False), reason="one text column and one number")


def _tile_run():
    from nl2sql.dashboards.models import TileRun

    return TileRun(tile_id="t1", at=NOW, status="ok", trace_id="tr", chart=_spec(), explanation=None,
                   filtered=False, error=None)


def _models():
    """Every Wave C model, as (class, a valid instance)."""
    from nl2sql.api.query_api import QueryResult
    from nl2sql.chat_tools import ChatToolInfo, ToolOutput
    from nl2sql.charts.spec import ChartData, Encoding
    from nl2sql.conversations.models import Conversation, ConversationInfo, ModelStep, ToolCall, ToolStep, Turn
    from nl2sql.dashboards import models as d

    call = ToolCall(name="ask", args={"question": "How many customers?"})
    step = ToolStep(call=call, status="ok", question="How many customers?", trace_id="t", result=QueryResult(),
                    output=None)
    turn = Turn(index=1, at=NOW, kind="message", text="hi", reply="hello", steps=[step], status="answered")
    layout = d.TileLayout(x=0, y=0, w=2, h=2)
    plan = d.ChartPlan(decomposition={"sub_queries": []}, plans={"sq1": {}})
    tile = d.Tile(id="t1", title="Revenue", question="q", plan=plan, layout=layout)
    identity = d.ScheduleIdentity(user_id="u", tenant_id=None, roles=["analyst"])
    schedule = d.Schedule(every_minutes=60, identity=identity, set_at=NOW, next_at=NOW, state="active")
    dashboard = d.Dashboard(id="d1", title="Sales", workspace_id="w1", status="draft", conversation_id=None,
                            tenant_id=None, user_id="u", tiles=[tile], created_at=NOW, updated_at=NOW)
    run = _tile_run()
    return [
        (ToolCall, call), (ToolStep, step), (Turn, turn), (ModelStep, ModelStep(reply="hi")),
        (Conversation, Conversation(id="c1", tenant_id=None, user_id="u", title="t", datasource_id=None,
                                    created_at=NOW, updated_at=NOW, turns=[turn], pending=None)),
        (ConversationInfo, ConversationInfo(id="c1", title="t", updated_at=NOW, turns=1)),
        (ToolOutput, ToolOutput(kind="markdown", title="Report", body="# x")),
        (ChatToolInfo, ChatToolInfo(name="chart", description="d", args_schema={})),
        (Encoding, Encoding(field="a", kind="number", title="A")),
        (ChartData, ChartData(trace_id="t", result_id="n", columns=["a"], rows=[[1]], truncated=False)),
        (type(_spec()), _spec()),
        (d.Workspace, d.Workspace(id="w1", name="General", tenant_id=None, user_id="u", created_at=NOW)),
        (d.ChartPlan, plan), (d.StoredQuery, d.StoredQuery(sub_query_id="sq1", datasource_id="ds", plan={})),
        (d.TileLayout, layout), (d.Tile, tile),
        (d.DashboardFilter, d.DashboardFilter(id="f1", label="Year", column="ds.main.t.year", kind="number",
                                              op="in")),
        (d.ScheduleIdentity, identity), (d.Schedule, schedule), (d.Dashboard, dashboard), (d.TileRun, run),
        (d.SkippedTile, d.SkippedTile(title="t", status="empty", reason="no rows")),
        (d.DashboardRun, d.DashboardRun(dashboard_id="d1", at=NOW, trigger="manual", filters={}, tiles=[run],
                                        usage=QueryResult().usage)),
        (d.DashboardView, d.DashboardView(dashboard=dashboard, runs=[run], refreshed_at=NOW)),
        (d.DashboardInfo, d.DashboardInfo(id="d1", title="Sales", workspace_id="w1", status="draft", tiles=1,
                                          refreshed_at=None, updated_at=NOW)),
    ]


def test_wave_c_models_are_strict():
    for model, instance in _models():
        assert model.model_config.get("extra") == "forbid", model.__name__
        data = instance.model_dump(mode="json")
        with pytest.raises(ValidationError):
            model.model_validate({**data, "surprise": 1})
        assert model.model_validate_json(instance.model_dump_json()) == instance, f"{model.__name__} round trip"


def test_model_step_and_turn_vocabularies():
    from nl2sql.conversations.models import ModelStep, Turn

    assert set(get_args(Turn.model_fields["kind"].annotation)) == {"message", "choice", "retry", "tool"}
    assert set(get_args(Turn.model_fields["status"].annotation)) == {
        "answered", "needs_clarification", "needs_approval", "stopped", "error"}
    step = ModelStep(reply="hi")
    assert step.call is None and step.offers == []
    with pytest.raises(ValidationError):
        Turn.model_validate({"index": 1, "at": NOW, "kind": "chat", "text": "", "steps": [], "status": "answered"})


def test_a_stored_step_holds_a_result_without_rows():
    from nl2sql.api.query_api import QueryResult, RowSample, SubQueryResult
    from nl2sql.conversations.models import ToolCall, ToolStep

    result = QueryResult(sub_queries=[SubQueryResult(id="sq1", rows=RowSample(columns=["n"], rows=[[1]],
                                                                             total_rows=1))])
    step = ToolStep(call=ToolCall(name="ask"), status="ok", result=result)
    assert step.result.sub_queries[0].rows is not None, "stripping rows is the conversation store's job (C1)"


def test_the_chart_spec_has_four_kinds():
    from nl2sql.charts.spec import ChartSpec, ColumnKind

    assert set(get_args(ColumnKind)) == {"number", "text", "temporal", "boolean", "other"}
    assert set(get_args(ChartSpec.model_fields["kind"].annotation)) == {"line", "bar", "big_number", "table"}


def test_a_chat_tool_is_a_protocol_a_plugin_satisfies():
    from nl2sql.chat_tools import ChatTool, ToolContext, ToolNotApplicable, ToolOutput

    class Args(BaseModel):
        turn: int | None = None

    class Echo:
        interface_version: ClassVar[int] = 1
        description: ClassVar[str] = "Echo."
        args_model: ClassVar[type[BaseModel]] = Args

        def __call__(self, ctx, args):
            return ToolOutput(kind="json", title="Echo", body={"turn": args.turn})

    assert isinstance(Echo(), ChatTool)
    assert not isinstance(object(), ChatTool)
    assert Echo()(None, Args(turn=2)).body == {"turn": 2}
    assert issubclass(ToolNotApplicable, Exception)
    assert ToolContext.model_config.get("frozen") is True
    with pytest.raises(ValidationError):
        ToolOutput(kind="pdf", title="x", body="x")


def test_dashboard_edit_union_is_strict():
    from nl2sql.dashboards.models import EDIT_KINDS, DashboardEdit

    assert EDIT_KINDS == ("rename", "rename_tile", "set_tile_chart", "remove_tile", "arrange", "set_filters",
                          "set_schedule", "move", "add_tile", "replace_plan")
    edits = TypeAdapter(list[DashboardEdit])
    valid = [
        {"kind": "rename", "title": "Sales"},
        {"kind": "rename_tile", "tile_id": "t1", "title": "Revenue"},
        {"kind": "set_tile_chart", "tile_id": "t1", "chart": "bar"},
        {"kind": "remove_tile", "tile_id": "t1"},
        {"kind": "arrange", "layouts": [{"tile_id": "t1", "x": 0, "y": 0, "w": 2, "h": 2}]},
        {"kind": "set_filters", "filters": [{"id": "f", "label": "L", "column": "d.s.t.c", "kind": "text",
                                             "op": "in"}]},
        {"kind": "set_schedule", "every_minutes": 60},
        {"kind": "set_schedule", "every_minutes": None},
        {"kind": "move", "workspace_id": "w2"},
        {"kind": "add_tile", "title": "Revenue", "from_step": {"turn": 1, "step": 1}},
        {"kind": "replace_plan", "tile_id": "t1", "from_step": {"turn": 2, "step": 1}},
    ]
    parsed = edits.validate_python(valid)
    assert [e.kind for e in parsed] == [v["kind"] for v in valid]
    assert {v["kind"] for v in valid} == set(EDIT_KINDS), "every kind is exercised"
    assert edits.validate_python(edits.dump_python(parsed, mode="json")) == parsed
    for bad in ({"kind": "delete_everything"}, {"title": "no kind"}, {"kind": "rename"},
                {"kind": "rename", "title": "x", "extra": 1}, {"kind": "set_tile_chart", "tile_id": "t",
                                                               "chart": "pie"},
                {"kind": "add_tile", "title": "t", "from_step": {"turn": 1}},
                {"kind": "add_tile", "title": "t", "from_step": {"turn": 1, "step": 1, "x": 2}}):
        with pytest.raises(ValidationError):
            edits.validate_python([bad])


def test_default_workspace_and_schedule_models_round_trip():
    from nl2sql.dashboards.models import DEFAULT_WORKSPACE_NAME, Schedule, ScheduleIdentity, Workspace

    assert DEFAULT_WORKSPACE_NAME == "General"
    workspace = Workspace(id="w1", name=DEFAULT_WORKSPACE_NAME, tenant_id="acme", user_id="ada", created_at=NOW)
    assert Workspace.model_validate_json(workspace.model_dump_json()) == workspace
    schedule = Schedule(every_minutes=15, identity=ScheduleIdentity(user_id="ada", tenant_id="acme",
                                                                    roles=["analyst", "viewer"]),
                        set_at=NOW, next_at=None, state="stopped",
                        error={"code": "SCHEDULE_IDENTITY_EXPIRED", "message": "Set it again."})
    again = Schedule.model_validate_json(schedule.model_dump_json())
    assert again == schedule and again.identity.roles == ["analyst", "viewer"] and again.next_at is None
    assert again.error["code"] == "SCHEDULE_IDENTITY_EXPIRED"
    with pytest.raises(ValidationError):
        Schedule(every_minutes=0, identity=schedule.identity, set_at=NOW, next_at=None, state="active")
    with pytest.raises(ValidationError):
        Schedule(every_minutes=15, identity=schedule.identity, set_at=NOW, next_at=None, state="paused")


def test_a_chart_plan_holds_one_whole_run_or_a_verified_query():
    from nl2sql.dashboards.models import ChartPlan
    from nl2sql.hooks.approval import VerifiedRef

    whole = ChartPlan(decomposition=_decomposition().model_dump(mode="json"), plans={"sq1": {"tables": []}})
    assert ChartPlan.model_validate_json(whole.model_dump_json()) == whole
    verified = ChartPlan(verified=VerifiedRef(id="v1", params={"year": 2013}), verified_datasource_id="ds1")
    assert verified.verified.params == {"year": 2013}
    for bad in ({}, {"decomposition": {}, "plans": {}, "verified": VerifiedRef(id="v")},
                {"verified": VerifiedRef(id="v")},  # a verified query names its datasource
                {"decomposition": {"sub_queries": []}}, {"plans": {"sq1": {}}}):
        with pytest.raises(ValidationError):
            ChartPlan(**bad)


def test_a_stored_query_holds_a_plan_or_a_verified_query():
    from nl2sql.dashboards.models import StoredQuery
    from nl2sql.hooks.approval import VerifiedRef

    assert StoredQuery(sub_query_id="sq1", datasource_id="ds", plan={"tables": []}).verified is None
    assert StoredQuery(sub_query_id="sq1", datasource_id="ds", verified=VerifiedRef(id="v1")).plan is None
    for bad in ({}, {"plan": {}, "verified": VerifiedRef(id="v1")}):
        with pytest.raises(ValidationError):
            StoredQuery(sub_query_id="sq1", datasource_id="ds", **bad)


def test_a_tile_layout_stays_on_the_four_column_grid():
    from nl2sql.dashboards.models import TileLayout

    assert TileLayout(x=3, y=0, w=1, h=1).x == 3 and TileLayout(x=0, y=0, w=4, h=1).w == 4
    assert TileLayout(x=2, y=0, w=2, h=1).w == 2
    for x, w in ((3, 2), (1, 4), (2, 3)):
        with pytest.raises(ValidationError):
            TileLayout(x=x, y=0, w=w, h=1)


def test_a_model_step_is_exactly_one_of_a_reply_and_a_call():
    from nl2sql.conversations.models import ModelStep, ToolCall

    call = ToolCall(name="ask", args={"question": "q"})
    assert ModelStep(reply="hi").call is None and ModelStep(call=call).reply is None
    assert ModelStep(reply="hi", offers=[call]).offers == [call], "offers go with a reply"
    for bad in ({}, {"reply": "hi", "call": call}, {"call": call, "offers": [call]}):
        with pytest.raises(ValidationError):
            ModelStep(**bad)


def test_api_key_events_say_whose_key_it_was():
    from nl2sql.events.catalog import ApiKeyCreated, ApiKeyRevoked

    revoked = ApiKeyRevoked(key_id="ab12cd34", user_id="ada", tenant_id="acme", roles=["admin"], by="root")
    assert set(ApiKeyRevoked.model_fields) == set(ApiKeyCreated.model_fields)
    assert (revoked.user_id, revoked.tenant_id, revoked.roles) == ("ada", "acme", ["admin"])


def test_api_types_candidate_and_api_key():
    from nl2sql.api.types import ApiKey, Candidate
    from nl2sql_sdk.discovery import Found

    candidate = Candidate(type="sqlite", title="SQLite", found=Found(kind="file", value="./sales.db"),
                          connection={"path": "./sales.db", "password": "hunter2"}, missing=[], installed=True,
                          install_hint="")
    assert "hunter2" not in repr(candidate) and "hunter2" not in str(candidate), "repr masks secret fields"
    assert candidate.connection["password"] == "hunter2", "the value itself is kept"
    key = ApiKey(id="ab12cd34", label="ci", user_id="u", tenant_id=None, roles=["analyst"], created_at=NOW,
                 created_by="admin", revoked_at=None)
    assert ApiKey.model_validate_json(key.model_dump_json()) == key
    assert "secret" not in ApiKey.model_fields and "hash" not in ApiKey.model_fields, "never the secret"
    with pytest.raises(ValidationError):
        ApiKey.model_validate({**key.model_dump(), "secret": "x"})


def test_discoverable_adapter_is_an_optional_protocol():
    from nl2sql_sdk.discovery import DiscoverableAdapter, Found

    class Finds:
        file_suffixes = (".sqlite",)
        default_ports = ()
        url_schemes = ("sqlite",)

        @classmethod
        def connection_for(cls, found):
            return {"path": found.value} if found.kind == "file" else None

    assert isinstance(Finds, DiscoverableAdapter)
    assert not isinstance(type("Plain", (), {}), DiscoverableAdapter), "an adapter without it is never found"
    assert Finds.connection_for(Found(kind="file", value="a.sqlite")) == {"path": "a.sqlite"}
    with pytest.raises(ValidationError):
        Found(kind="socket", value="x")
    with pytest.raises(ValidationError):
        Found(kind="file", value="a.sqlite", surprise=1)


def test_settings_charts_are_part_of_settings():
    assert Settings().charts.max_categories == 20
    assert Conversations().context_turns == 3 and Dashboards().max_tiles == 10 and Mcp().auth == "api_keys"
