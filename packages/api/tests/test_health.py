"""``/health``, ``/ready``, ``/meta`` and ``/about`` (docs/architecture/wave_d.md, "Health and readiness")."""
from importlib.metadata import version

import pytest
from dashboard_stubs import Recorder

from nl2sql import CheckReport, DatasourceInfo, DatasourceStatus, EngineNotReady, UserContext
from nl2sql_api.routes import health

API = "/api/v1"
ADMIN = UserContext(roles=["admin"])
ANN = UserContext(user_id="ann", roles=["analyst"])


def up(name="chinook", **kw):
    return DatasourceStatus(id=name, status="ready", **kw)


def down(name="lost", **kw):
    return DatasourceStatus(id=name, status="failed", stage="connect", reason="password=hunter2 refused", **kw)


class Checking:
    """``engine.check()``: answers the report it holds, raising ``EngineNotReady`` as the engine does."""

    def __init__(self, report):
        self.report, self.calls = report, 0

    def __call__(self):
        self.calls += 1
        required = [d.id for d in self.report.datasources if d.required and d.status == "failed"]
        if required or (self.report.datasources and not any(d.status == "ready" for d in self.report.datasources)):
            raise EngineNotReady("not ready", self.report)
        return self.report


@pytest.fixture(autouse=True)
def no_cache():
    health.forget_ready()
    yield
    health.forget_ready()


@pytest.fixture
def stub(api_client):
    def make(report, **sections):
        # `optional_caller` resolves the identity itself, so these run through the real `api.auth`: an admin by default.
        sections = {"authenticated": False, "api": {"auth": "static", "static_role": "admin"}, **sections}
        client, engine = api_client(**sections)
        engine.check = Checking(report)
        engine.llm = Recorder()
        engine.llm.returns["list"] = {"default": {"provider": "openai"}, "decomposer": {"provider": "openai"}}
        engine.llm.returns.update({"recorded": [], "recorded_with": None})
        engine.datasources = Recorder()
        engine.datasources.returns["list"] = []
        return client, engine

    return make


def test_health_is_liveness_with_the_versions(api_client):
    client, _ = api_client()
    response = client.get(f"{API}/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": {"engine": version("nl2sql-engine"),
                                                           "api": version("nl2sql-api")}}


def test_ready_passes_when_the_engine_is_ready(stub):
    client, _ = stub(CheckReport(datasources=[up()], index="ok"))

    response = client.get(f"{API}/ready")

    assert response.status_code == 200 and response.json() == {"status": "ready", "problems": []}


def test_ready_fails_on_a_required_datasource(stub):
    client, _ = stub(CheckReport(datasources=[up(), down(required=True)], index="ok"))

    response = client.get(f"{API}/ready")

    assert response.status_code == 503 and response.json()["status"] == "not_ready"


def test_ready_passes_with_an_optional_failure(stub):
    client, _ = stub(CheckReport(datasources=[up(), down(required=False)], index="ok"))

    response = client.get(f"{API}/ready")

    assert response.status_code == 200
    assert response.json()["problems"] == ["Datasource 'lost' failed at connect."]  # a problem, not "not ready"


def test_ready_fails_on_a_missing_index(stub):
    client, _ = stub(CheckReport(datasources=[up()], index="missing"))

    assert client.get(f"{API}/ready").status_code == 503


def test_ready_fails_on_a_missing_key_and_passes_with_request_keys(stub):
    report = CheckReport(datasources=[up()], llm={"default": "OPENAI_API_KEY is not set"}, index="ok")
    client, _ = stub(report)
    assert client.get(f"{API}/ready").status_code == 503

    health.forget_ready()
    client, _ = stub(report, api={"auth": "static", "static_role": "admin", "request_llm": True})
    assert client.get(f"{API}/ready").status_code == 200


def test_ready_passes_when_every_agent_is_on_replay(stub):
    report = CheckReport(datasources=[up()], llm={"default": "no key"}, index="ok")
    client, engine = stub(report)
    engine.llm.returns["list"] = {"default": {"provider": "replay"}, "decomposer": {"provider": "replay"}}

    assert client.get(f"{API}/ready").status_code == 200


def test_ready_details_only_for_admin(stub):
    report = CheckReport(datasources=[up(), down(required=True)], llm={"default": "OPENAI_API_KEY is not set"},
                         index="missing")
    for api in ({"auth": "static", "static_role": "analyst"}, {"auth": "api_keys"}):
        health.forget_ready()
        visitor, _ = stub(report, authenticated=False, api=api)
        assert visitor.get(f"{API}/ready").json() == {"status": "not_ready"}, "a visitor learns the status only"

    health.forget_ready()
    admin, _ = stub(report)
    body = admin.get(f"{API}/ready").json()
    assert body["status"] == "not_ready"
    assert "Datasource 'lost' (required) failed at connect." in body["problems"]
    assert "The index is missing." in body["problems"]
    assert "hunter2" not in str(body)  # never a reason's text


def test_ready_is_cached_for_ten_seconds(stub):
    client, engine = stub(CheckReport(datasources=[up()], index="ok"))

    for _ in range(3):
        client.get(f"{API}/ready")
    assert engine.check.calls == 1

    health.forget_ready()
    client.get(f"{API}/ready")
    assert engine.check.calls == 2
    assert health.READY_TTL_S == 10


def test_meta_without_a_caller_lists_no_datasource(stub):
    client, engine = stub(CheckReport(), authenticated=False, api={"auth": "api_keys", "request_llm": True},
                          mode="demo")

    response = client.get(f"{API}/meta")

    assert response.status_code == 200
    assert response.json() == {"mode": "demo", "auth": "api_keys", "request_llm": True}
    assert engine.datasources.calls == []


def test_meta_reports_the_mode(stub):
    for mode in ("demo", "hosted", "your_data"):
        client, _ = stub(CheckReport(), authenticated=False, mode=mode)
        assert client.get(f"{API}/meta").json()["mode"] == mode


def test_meta_lists_sample_questions_and_recorded(stub):
    client, engine = stub(CheckReport(), authenticated=False, api={"auth": "static", "static_role": "analyst"})
    engine.datasources.returns["list"] = [
        DatasourceInfo(id="chinook", type="sqlite", description="Music", status="ready", connection={"database": "x"},
                       sample_questions=["How many tracks?", "Top artists?"]),
        DatasourceInfo(id="support", type="sqlite", description="Tickets", status="ready", sample_questions=[])]
    engine.llm.returns["recorded"] = lambda questions: [q for q in questions if q == "How many tracks?"]
    engine.llm.returns["recorded_with"] = {"provider": "openai", "model": "gpt-x"}

    body = client.get(f"{API}/meta").json()

    assert body["datasources"] == [
        {"id": "chinook", "description": "Music", "sample_questions": ["How many tracks?", "Top artists?"],
         "recorded": ["How many tracks?"]},
        {"id": "support", "description": "Tickets", "sample_questions": [], "recorded": []}]
    assert body["recorded_with"] == {"provider": "openai", "model": "gpt-x"}
    assert "connection" not in str(body["datasources"])
    assert engine.datasources.called("list") == [((), {"user_context": UserContext(user_id="static", roles=["analyst"])})]


def test_about_is_the_engines_description(api_client):
    client, engine = api_client()
    engine.about = lambda: {"version": "1.2.3", "steps": []}
    assert client.get(f"{API}/about").json() == {"version": "1.2.3", "steps": []}
