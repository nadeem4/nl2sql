"""The MCP server's three tools over an in-process MCP client (wave_a.md, section 12).

The engine is the real facade over a stub runtime: the real ``datasources.schema``
and ``policies.scope``, a scripted ``run_query``. The run end to end, on a fake
LLM, is ``tests/e2e/test_mcp_fake_llm.py``.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp")
import anyio  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from nl2sql import NL2SQL, Policies, QueryResult, SubQueryResult, RowSample, UserContext  # noqa: E402
from nl2sql.mcp.server import PREVIEW_ROWS, build_server  # noqa: E402
from nl2sql.policies import PolicyEngine  # noqa: E402
from nl2sql.testing.facade import StubDatasources  # noqa: E402
from nl2sql_sdk.schema import (  # noqa: E402
    ColumnContract,
    ForeignKeyContract,
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
)

ADMIN = UserContext(roles=["admin"])
VIEWER = UserContext(user_id="ana", roles=["viewer"])


def _snapshot() -> SchemaSnapshot:
    album = TableRef(schema_name="main", table_name="Album")
    artist = TableRef(schema_name="main", table_name="Artist")
    contract = SchemaContract(datasource_id="chinook", engine_type="sqlite", tables={
        album.full_name: TableContract(
            table=album,
            columns={"AlbumId": ColumnContract(name="AlbumId", data_type="INTEGER", is_nullable=False,
                                               is_primary_key=True),
                     "ArtistId": ColumnContract(name="ArtistId", data_type="INTEGER", is_nullable=False)},
            foreign_keys=[ForeignKeyContract(constrained_columns=["ArtistId"], referred_table=artist,
                                             referred_columns=["ArtistId"])]),
        artist.full_name: TableContract(
            table=artist,
            columns={"Name": ColumnContract(name="Name", data_type="TEXT", is_nullable=True)}),
    })
    metadata = SchemaMetadata(datasource_id="chinook", engine_type="sqlite", tables={
        album.full_name: TableMetadata(table=album, row_count=347, description="One row per album.")})
    return SchemaSnapshot(contract=contract, metadata=metadata)


class _Index:
    def snapshot(self, datasource_id):
        return _snapshot() if datasource_id == "chinook" else None


class _Runtime:
    def __init__(self):
        self.vector_store = _Index()
        # admin reads everything; viewer reads chinook but not Artist, and nothing of support.
        self.policy_engine = PolicyEngine(Policies(roles={
            "admin": {"description": "Everything", "allow": ["*"]},
            "viewer": {"description": "The catalog", "allow": ["chinook"], "deny": ["chinook.*.Artist"]},
        }))


class _Engine(NL2SQL):
    """The real facade over a stub runtime; ``run_query`` answers with ``answer``."""

    def __init__(self, answer=None):
        self._runtime = _Runtime()
        self.answer = answer if answer is not None else QueryResult(status="success")
        self.calls = []

    @property
    def datasources(self):
        return StubDatasources(self._runtime, ["chinook", "support"])

    def run_query(self, question, *, datasource_id=None, user_context=None, **kwargs):
        self.calls.append((question, datasource_id, user_context))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _call(engine, identity, tool, **arguments):
    """The tool's structured result, through a real MCP client session."""
    async def go():
        async with create_connected_server_and_client_session(build_server(engine, identity)) as client:
            return await client.call_tool(tool, arguments)

    result = anyio.run(go)
    assert not result.isError, result.content
    return result.structuredContent


def test_the_server_offers_exactly_the_three_tools():
    async def go():
        async with create_connected_server_and_client_session(build_server(_Engine(), ADMIN)) as client:
            return await client.list_tools()

    assert sorted(t.name for t in anyio.run(go).tools) == ["ask", "describe_table", "list_datasources"]


def test_list_datasources_hides_unreachable_ones():
    viewer = _call(_Engine(), VIEWER, "list_datasources")
    admin = _call(_Engine(), ADMIN, "list_datasources")

    assert [d["id"] for d in viewer["datasources"]] == ["chinook"]
    assert [d["id"] for d in admin["datasources"]] == ["chinook", "support"]
    assert set(viewer["datasources"][0]) == {"id", "type", "description", "status"}


def test_describe_table_returns_what_the_caller_may_read():
    body = _call(_Engine(), VIEWER, "describe_table", datasource_id="chinook", table="album")

    assert body["status"] == "ok"
    table = body["table"]
    assert table["name"] == "Album" and table["row_count"] == 347
    assert [c["name"] for c in table["columns"]] == ["AlbumId", "ArtistId"]
    # The foreign key names a table the viewer may not read, so it is left out.
    assert table["foreign_keys"] == []


def test_describe_table_hides_a_denied_table_as_not_found():
    """A denied table, a missing one and a table in an unreachable datasource answer alike."""
    denied = _call(_Engine(), VIEWER, "describe_table", datasource_id="chinook", table="Artist")
    missing = _call(_Engine(), VIEWER, "describe_table", datasource_id="chinook", table="Nope")
    elsewhere = _call(_Engine(), VIEWER, "describe_table", datasource_id="support", table="Ticket")

    assert denied["status"] == missing["status"] == elsewhere["status"] == "not_found"
    assert denied["message"].replace("Artist", "X") == missing["message"].replace("Nope", "X")
    assert denied["tables"] == missing["tables"] == ["Album"]
    assert "Artist" not in json.dumps(denied["tables"])
    assert elsewhere["tables"] == []
    assert _call(_Engine(), ADMIN, "describe_table", datasource_id="chinook", table="Artist")["status"] == "ok"


def _rows(n):
    return RowSample(columns=["artist", "albums"], rows=[[f"a{i}", i] for i in range(n)], total_rows=n)


def test_ask_returns_answer_sql_and_capped_rows():
    engine = _Engine(QueryResult(
        status="success", trace_id="t-1",
        final_answer={"summary": "Albums per artist.", "content": "Iron Maiden has the most.", "format_type": "text"},
        sub_queries=[SubQueryResult(id="sq1", datasource_id="chinook", sql="SELECT 1", rows=_rows(30),
                                    status="success")]))

    body = _call(engine, VIEWER, "ask", question="Albums per artist?")

    assert engine.calls == [("Albums per artist?", None, VIEWER)]
    assert body["status"] == "success" and body["trace_id"] == "t-1"
    assert body["answer"] == {"summary": "Albums per artist.", "content": "Iron Maiden has the most."}
    [sq] = body["sub_queries"]
    assert sq["sql"] == "SELECT 1" and sq["columns"] == ["artist", "albums"]
    assert len(sq["rows"]) == PREVIEW_ROWS == 20 and sq["total_rows"] == 30
    assert sq["truncated"] is True and body["truncated"] is True
    assert body["source"]["type"] == "generated"
    assert "errors" not in body and "approvals_needed" not in body and "stop" not in body


def test_ask_reports_needs_approval_as_a_structured_result():
    from nl2sql import ApprovalRequest

    request = ApprovalRequest.build(question="Every invoice line?", asker=ADMIN, hook="big_tables",
                                    datasource_id="chinook", sql="SELECT * FROM InvoiceLine",
                                    reason="It reads every invoice line.")
    engine = _Engine(QueryResult(
        status="needs_approval", trace_id="t-2", approvals_needed=[request],
        errors=[{"node": "executor", "message": "An approver must allow this query.",
                 "error_code": "NEEDS_APPROVAL", "severity": "ERROR"}]))

    body = _call(engine, ADMIN, "ask", question="Every invoice line?")

    assert body["status"] == "needs_approval"
    assert body["approvals_needed"][0]["id"] == request.id
    assert body["approvals_needed"][0]["reason"] == "It reads every invoice line."
    assert body["errors"] == [{"code": "NEEDS_APPROVAL", "node": "executor",
                               "message": "An approver must allow this query."}]


def test_ask_never_returns_exception_text():
    """A run's crash, and an exception out of the engine, are reported by code, not by their text."""
    crashed = _Engine(QueryResult(status="error", trace_id="t-3", errors=[
        {"node": "executor", "message": "Executor crash: password=hunter2", "error_code": "EXECUTOR_CRASH",
         "severity": "ERROR"},
        {"node": "logical_validator", "message": "You do not have permission to see the data.",
         "error_code": "SECURITY_VIOLATION", "severity": "ERROR"}], warnings=[
        # An earlier attempt a retry recovered from, and an answer check's own warning.
        {"node": "executor", "message": "no such column: password=hunter2", "error_code": "EXECUTION_FAILED",
         "severity": "ERROR", "sub_query_id": "sq1"},
        {"node": "aggregator", "message": "Sub-query 'sq1' returned 1000 rows, the row cap.",
         "error_code": "RESULT_TRUNCATED", "severity": "WARNING"}]))
    raised = _Engine(RuntimeError("could not connect to postgres://admin:hunter2@db"))

    run = _call(crashed, ADMIN, "ask", question="q")
    out = _call(raised, ADMIN, "ask", question="q")

    assert [e["code"] for e in run["errors"]] == ["EXECUTOR_CRASH", "SECURITY_VIOLATION"]
    assert "t-3" in run["errors"][0]["message"]
    assert run["warnings"] == [
        {"code": "EXECUTION_FAILED", "message": "EXECUTION_FAILED: the trace t-3 has the details."},
        {"code": "RESULT_TRUNCATED", "message": "Sub-query 'sq1' returned 1000 rows, the row cap."}]
    assert out["status"] == "error" and out["errors"][0]["code"] == "INTERNAL_ERROR"
    assert "hunter2" not in json.dumps(run) + json.dumps(out)


def test_ask_on_an_unreachable_datasource_is_not_found_and_never_runs():
    engine = _Engine()

    body = _call(engine, VIEWER, "ask", question="q", datasource_id="support")

    assert body["status"] == "error"
    assert body["errors"] == [{"code": "DATASOURCE_NOT_FOUND", "node": None,
                               "message": "No datasource 'support'."}]
    assert engine.calls == []


def test_the_identity_is_resolved_on_every_call():
    """``identity`` may be a function, so a server over HTTP can name a caller per request (Wave C)."""
    callers = iter([ADMIN, VIEWER])
    engine = _Engine()

    async def go():
        async with create_connected_server_and_client_session(build_server(engine, lambda: next(callers))) as client:
            first = await client.call_tool("list_datasources", {})
            second = await client.call_tool("list_datasources", {})
            return first.structuredContent, second.structuredContent

    first, second = anyio.run(go)
    assert len(first["datasources"]) == 2 and len(second["datasources"]) == 1


def test_every_caller_facing_code_is_an_error_code():
    from nl2sql import ErrorCode
    from nl2sql.mcp.server import _CALLER_FACING

    assert _CALLER_FACING <= {code.value for code in ErrorCode}


def test_ask_never_names_a_denied_table():
    """A refusal is always the generic one over MCP, whatever ``refusal_names_tables`` says."""
    engine = _Engine(QueryResult(status="error", trace_id="t-4", errors=[
        {"node": "logical_validator", "message": "Role 'viewer' denied access to 'chinook.Customer'",
         "error_code": "SECURITY_VIOLATION", "severity": "ERROR"}]))

    body = _call(engine, VIEWER, "ask", question="Who are the top customers?")

    assert body["errors"] == [{"code": "SECURITY_VIOLATION", "node": "logical_validator",
                               "message": "You do not have permission to see the data this question requires."}]
    assert "Customer" not in json.dumps(body)


@pytest.mark.parametrize("tool, arguments", [
    ("ask", {"question": "q"}),
    ("list_datasources", {}),
    ("describe_table", {"datasource_id": "chinook", "table": "Album"}),
])
def test_an_identity_that_raises_returns_no_exception_text(tool, arguments):
    """Wave C resolves a caller per request; a resolver's failure (a rejected token, say) is never echoed."""
    def rejected():
        raise RuntimeError("token=SECRET123 rejected")

    engine = _Engine()
    body = _call(engine, rejected, tool, **arguments)

    assert body == {"status": "error", "errors": [
        {"code": "INTERNAL_ERROR", "node": None, "message": "The server could not answer. Its log has the details."}]}
    assert engine.calls == []


def test_the_server_s_settings_come_from_code_not_the_environment(tmp_path, monkeypatch):
    """FastMCP's settings read FASTMCP_* variables and ./.env; every one is passed explicitly, so neither applies."""
    (tmp_path / ".env").write_text("FASTMCP_DEBUG=true" + chr(10) + "FASTMCP_LOG_LEVEL=DEBUG" + chr(10) + "FASTMCP_PORT=9" + chr(10),
                                        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FASTMCP_WARN_ON_DUPLICATE_TOOLS", "false")

    settings = build_server(_Engine(), ADMIN).settings

    assert settings.debug is False and settings.log_level == "INFO" and settings.port == 8000
    assert settings.warn_on_duplicate_tools is True
