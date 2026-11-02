"""The golden files the web app's node tests read: ``ApiError``, ``Job``, ``QueryResult``, ``Turn``, the ``Dashboard`` and
``DashboardView``, the ``Proposal`` and the ``DriftReport`` (a ``drifted`` one with its diff and impact, and an ``unchanged`` one) (docs/architecture/wave_d.md, "Testing").

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

from nl2sql import QueryResult, RowSample, SubQueryResult, ToolCall, ToolStep, Turn, UserContext
from nl2sql.testing.config import make_config
from nl2sql import DriftReport, Proposal
from nl2sql.schema.impact import DriftImpact, ImpactedQuery
from nl2sql_sdk.schema import ColumnChange, ColumnRef, SchemaDiff, TableRef
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.jobs import IndexJob, IndexResult, Jobs
from nl2sql_api.main import create_app

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "nl2sql-ui" / "src" / "api" / "fixtures"
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def build() -> dict:
    """``{file name: its JSON}``, from responses of an app with no real engine behind it."""
    ticks = count()
    with tempfile.TemporaryDirectory() as folder:
        class Conversations:
            """One stored turn that asked a question and was answered, as ``POST /conversations/{id}/turns`` serves it."""

            def ask(self, conversation_id, text=None, **kwargs):
                return Turn(index=1, at=START, kind="message", text=text, reply="There are 59 customers.",
                            steps=[ToolStep(call=ToolCall(name="ask", args={"question": text}), status="ok",
                                            question=text, trace_id="trace-0001-fixture")],
                            status="answered", trace_id="trace-0001-turn")

        class Engine:
            config = make_config(pathlib.Path(folder))
            conversations = Conversations()
            dashboards = Recorder()
            proposals = Recorder()
            index = Recorder()

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

        app = create_app()
        app.dependency_overrides[get_engine] = lambda: Engine
        app.dependency_overrides[identity.caller] = lambda: UserContext(user_id="ann", roles=["analyst"])
        app.dependency_overrides[identity.admin] = lambda: UserContext(user_id="ann", roles=["admin"])
        app.state.engine = Engine
        app.state.jobs = jobs = Jobs(clock=lambda: START + timedelta(seconds=next(ticks)), new_id=lambda: "job-0001-fixture-id",
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
            turn = client.post("/api/v1/conversations/conv-0001/turns", json={"text": "How many customers are there?"})
            assert turn.status_code == 200, turn.text
            return {"ApiError.json": error.json(), "Job.json": client.get(f"/api/v1/jobs/{job.id}").json(),
                    "QueryResult.json": answer.json(), "Turn.json": turn.json(),
                    "Dashboard.json": client.put("/api/v1/dashboards/d1/title", json={"title": "Sales overview"}).json(),
                    "DashboardView.json": client.get("/api/v1/dashboards/d1").json(),
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
