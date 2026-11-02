"""The golden files the web app's node tests read: ``ApiError``, ``Job``, ``QueryResult``, ``Turn``, the ``ChartSpec``s (a line of two series, a bar, a big number) and a ``ResultPage``, the ``Dashboard`` and
``DashboardView``, the ``Proposal``, the ``DriftReport`` (a ``drifted`` one with its diff and impact, and an ``unchanged`` one),
an eval ``Job`` (its result an ``EvalReport``) and ``/meta`` for a signed-in caller (docs/architecture/wave_d.md, "Testing").

Each is a real response of this API, so a route change that makes one stale fails here, as ``openapi.json``'s test does.
Rewrite them with ``python packages/api/tests/test_web_fixtures.py``. They live in ``nl2sql-ui/src/api/fixtures/``.
"""
import json
import pathlib
import sys
import time
import tempfile
from datetime import datetime, timedelta, timezone
from itertools import count

from dashboard_stubs import AT, Recorder, dashboard, view
from fastapi.testclient import TestClient

from nl2sql import ApprovalRequest, ChartData, ChartSpec, Encoding, QueryResult, ResultPage, RowSample, SubQueryResult, ToolCall, ToolStep, Turn, UserContext
from nl2sql.testing.config import make_config
from nl2sql import DatasourceInfo, DriftReport, EvalReport, Proposal
from nl2sql.schema.impact import DriftImpact, ImpactedQuery
from nl2sql.clarify.models import ClarificationOption, ClarificationRequest
from nl2sql.explain.models import ExplainedStep
from nl2sql_sdk.schema import ColumnChange, ColumnRef, SchemaDiff, TableRef
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.jobs import EvalJob, IndexJob, IndexResult, Jobs
from nl2sql_api.main import create_app

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "nl2sql-ui" / "src" / "api" / "fixtures"
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _waiting() -> dict:
    """Two turns that stopped for a person: a question with two readings, and one held for an approval."""
    reading = ExplainedStep(datasource_id="chinook", source="generated", text="Rank by revenue")
    clarification = ClarificationRequest.build(
        question="Top sales?", phrase="top sales", datasource_id="chinook", schema_version="v1", knowledge_digest="k",
        sub_query={}, options=[ClarificationOption(id="a", label="By revenue", plan={}, explanation=reading),
                               ClarificationOption(id="b", label="By units sold", plan={}, explanation=reading)])
    approval = ApprovalRequest(id="ap-1", question="Delete old rows?", hook="review", datasource_id="chinook",
                               sql="SELECT 1", sql_sha256="0" * 64, reason="A reviewer must approve this query.")

    def turn(status, text, **fields):
        return Turn(index=1, at=START, kind="message", text=text, status=status, steps=[ToolStep(
            call=ToolCall(name="ask", args={"question": text}), status=status, question=text, trace_id="trace-wait",
            result=QueryResult(trace_id="trace-wait", status=status, **fields))])

    return {"Top sales?": turn("needs_clarification", "Top sales?", clarification=clarification),
            "Run the report": turn("needs_approval", "Run the report", approvals_needed=[approval])}


WAITING = _waiting()


def build() -> dict:
    """``{file name: its JSON}``, from responses of an app with no real engine behind it."""
    ticks, ids = count(), count(1)
    with tempfile.TemporaryDirectory() as folder:
        class Conversations:
            """One stored turn that asked a question and was answered, as ``POST /conversations/{id}/turns`` serves it."""

            def ask(self, conversation_id, text=None, **kwargs):
                if text in WAITING:
                    return WAITING[text]
                return Turn(index=1, at=START, kind="message", text=text, reply="There are 59 customers.",
                            steps=[ToolStep(call=ToolCall(name="ask", args={"question": text}), status="ok",
                                            question=text, trace_id="trace-0001-fixture")],
                            status="answered", trace_id="trace-0001-turn")

        def encoding(field, kind):
            return Encoding(field=field, kind=kind, title=field.replace("_", " ").capitalize())

        def data(columns, rows, truncated=False):
            return ChartData(trace_id="trace-0001-fixture", result_id="sq1", columns=columns, rows=rows,
                             truncated=truncated)

        charts = {
            "line": ChartSpec(
                kind="line", title="Revenue by month per region", x=encoding("month", "temporal"),
                y=[encoding("revenue", "number")], series=encoding("region", "text"),
                data=data(["month", "region", "revenue"], [
                    ["2025-01-01", "East", 1200], ["2025-01-01", "West", 900], ["2025-02-01", "East", 1500],
                    ["2025-02-01", "West", 1100], ["2025-03-01", "East", 1450], ["2025-03-01", "West", 1700]]),
                takeaway="West overtook East in March.", reason="one date column, one number and one category"),
            "bar": ChartSpec(
                kind="bar", title="Top genres by tracks", x=encoding("genre", "text"), y=[encoding("tracks", "number")],
                data=data(["genre", "tracks"], [["Rock", 1297], ["Latin", 579], ["Metal", 374], ["Jazz", 130]],
                          truncated=True),
                takeaway="Rock has more tracks than the next two genres together.",
                reason="one category and one number"),
            "big_number": ChartSpec(
                kind="big_number", title="How many customers?", value=encoding("n", "number"),
                data=data(["n"], [[59]]), reason="one row and one number"),
        }

        class Results:
            @staticmethod
            def chart(trace_id, result_id, *, user_context, kind=None):
                return charts[kind or "line"]

            @staticmethod
            def page(trace_id, result_id, *, user_context, offset=0, limit=100):
                return ResultPage(request_id=trace_id, result_id=result_id, columns=["genre", "tracks"],
                                  rows=[["Rock", 1297], ["Latin", 579]], offset=offset, limit=limit, total_rows=4,
                                  next_offset=2)

        class Engine:
            results = Results()
            config = make_config(pathlib.Path(folder), api={"auth": "static", "static_role": "analyst"})
            conversations = Conversations()
            dashboards = Recorder()
            proposals = Recorder()
            index = Recorder()
            datasources = Recorder()
            llm = Recorder()

            @staticmethod
            def run_query(question, **_):
                return QueryResult(trace_id="trace-fixture", status="success", sub_queries=[SubQueryResult(
                    id="sq1", intent=question, sql="SELECT COUNT(*) AS n FROM Customer", datasource_id="chinook",
                    rows=RowSample(columns=["n"], rows=[[59]], total_rows=1), status="success")])

        Engine.dashboards.returns.update({"rename": dashboard(), "open": view()})
        Engine.proposals.returns["get"] = Proposal(
            id="p-1", kind="term", datasource_id="chinook", target="revenue",
            change={"definition": "Sum of invoice totals"}, origin="clarification", created_at=AT)
        tracks, orders = TableRef(schema_name="main", table_name="tracks"), TableRef(schema_name="main",
                                                                                 table_name="orders")
        diff = SchemaDiff(datasource_id="chinook", from_version="v1", tables_added=[orders],
                          columns_dropped=[ColumnRef(table=tracks, column_name="composer")],
                          columns_changed=[ColumnChange(table=tracks, column="unit_price",
                                                        changes={"type": ("NUMERIC(10,2)", "NUMERIC(12,2)")})])
        impact = DriftImpact(verified_queries=[ImpactedQuery(datasource_id="chinook", query_id="top_composers",
                                                             reasons=["reads dropped column tracks.composer"])],
                             preferred_tables=[])
        Engine.index.returns["check"] = [
            DriftReport(datasource_id="chinook", status="drifted", live_version="v1", checked_at=AT, diff=diff,
                        impact=impact),
            DriftReport(datasource_id="support", status="unchanged", live_version="v1", checked_at=AT)]

        Engine.datasources.returns["list"] = [DatasourceInfo(
            id="chinook", type="sqlite", status="ready", description="A music store", sample_questions=["How many customers?"])]
        Engine.llm.returns.update({"recorded": ["How many customers?"],
                                   "recorded_with": "anthropic/claude-sonnet-5-5"})

        app = create_app()
        app.dependency_overrides[get_engine] = lambda: Engine
        app.dependency_overrides[identity.caller] = lambda: UserContext(user_id="ann", roles=["analyst"])
        app.dependency_overrides[identity.admin] = lambda: UserContext(user_id="ann", roles=["admin"])
        app.state.engine = Engine
        app.state.jobs = jobs = Jobs(clock=lambda: START + timedelta(seconds=next(ticks)), new_id=lambda: f"job-{next(ids):04d}-fixture-id",
                                     poll_s=0.01)
        try:
            client = TestClient(app, raise_server_exceptions=False)
            answer = client.post("/api/v1/query", json={"question": "How many customers?"})
            assert answer.status_code == 200, answer.text
            error = client.get("/api/v1/jobs/missing")
            assert error.status_code == 404
            job = jobs.submit("index", IndexJob(datasource_ids=["chinook"]), UserContext(user_id="ann"),
                              lambda ctx: (ctx.progress("chinook", 1, 1),
                                           IndexResult(ok=True, stats=[{"datasource_id": "chinook"}]))[1])
            deadline = time.monotonic() + 5
            while jobs.get(job.id, UserContext(user_id="ann")).status != "succeeded":
                assert time.monotonic() < deadline, "the fixture job did not finish"
                time.sleep(0.005)
            report = EvalReport(mode="plans", suites=[{"name": "chinook", "path": "evals/chinook.yaml", "digest": "d1"}])
            evaluated = jobs.submit("eval", EvalJob(mode="plans"), UserContext(user_id="ann"),
                                    lambda ctx: (ctx.progress("case q1", 1, 1), report)[1])
            while jobs.get(evaluated.id, UserContext(user_id="ann")).status != "succeeded":
                assert time.monotonic() < deadline, "the fixture eval job did not finish"
                time.sleep(0.005)
            meta = client.get("/api/v1/meta")
            assert meta.status_code == 200 and "datasources" in meta.json(), meta.text
            chart_url = "/api/v1/results/trace-0001-fixture/sq1/chart"
            page = client.get("/api/v1/results/trace-0001-fixture/sq1?limit=2")
            assert page.status_code == 200, page.text
            turn = client.post("/api/v1/conversations/conv-0001/turns", json={"text": "How many customers are there?"})
            assert turn.status_code == 200, turn.text
            waiting = {name: client.post("/api/v1/conversations/conv-0001/turns", json={"text": text}).json()
                       for name, text in (("TurnClarification.json", "Top sales?"), ("TurnApproval.json", "Run the report"))}
            return {**waiting, "ApiError.json": error.json(), "Job.json": client.get(f"/api/v1/jobs/{job.id}").json(),
                    "JobEval.json": client.get(f"/api/v1/jobs/{evaluated.id}").json(), "Meta.json": meta.json(),
                    "QueryResult.json": answer.json(), "Turn.json": turn.json(),
                    "Dashboard.json": client.put("/api/v1/dashboards/d1/title", json={"title": "Sales overview"}).json(),
                    "DashboardView.json": client.get("/api/v1/dashboards/d1").json(),
                    "ChartLine.json": client.get(chart_url).json(),
                    "ChartBar.json": client.get(chart_url, params={"kind": "bar"}).json(),
                    "ChartBigNumber.json": client.get(chart_url, params={"kind": "big_number"}).json(),
                    "ResultPage.json": page.json(),
                    "Proposal.json": client.get("/api/v1/proposals/p-1").json(),
                    "DriftReport.json": client.get("/api/v1/index/check").json()[0],
                    "DriftReportUnchanged.json": client.get("/api/v1/index/check").json()[1]}
        finally:
            jobs.close()


def _text(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def test_web_fixtures_are_current():
    for name, data in build().items():
        path = FIXTURES / name
        assert path.exists() and path.read_text(encoding="utf-8") == _text(data), (
            f"{path} is stale. Rewrite it with: python packages/api/tests/test_web_fixtures.py")


if __name__ == "__main__":
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, data in build().items():
        (FIXTURES / name).write_text(_text(data), encoding="utf-8", newline="\n")
        print(f"wrote {FIXTURES / name}", file=sys.stderr)
