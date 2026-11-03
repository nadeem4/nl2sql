"""``/health``, ``/ready``, ``/meta`` and ``/about``."""
from importlib.metadata import version
from types import SimpleNamespace

import pytest
from dashboard_stubs import Recorder
from fastapi.testclient import TestClient

from nl2sql import CheckReport, DatasourceInfo, DatasourceStatus, EngineNotReady, UserContext
from nl2sql_api.main import app
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
    health.forget_ready(app)
    yield
    health.forget_ready(app)


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

    health.forget_ready(app)
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
        health.forget_ready(app)
        visitor, _ = stub(report, authenticated=False, api=api)
        assert visitor.get(f"{API}/ready").json() == {"status": "not_ready"}, "a visitor learns the status only"

    health.forget_ready(app)
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

    health.forget_ready(app)
    client.get(f"{API}/ready")
    assert engine.check.calls == 2
    assert health.READY_TTL_S == 10


def test_meta_without_a_caller_lists_no_datasource(stub):
    client, engine = stub(CheckReport(), authenticated=False, api={"auth": "api_keys", "request_llm": True},
                          mode="demo")

    response = client.get(f"{API}/meta")

    assert response.status_code == 200
    assert response.json() == {"mode": "demo", "auth": "api_keys", "request_llm": True, "start": None}
    assert engine.datasources.calls == []


def test_meta_reports_the_mode(stub):
    for mode in ("demo", "hosted", "your_data"):
        client, _ = stub(CheckReport(), authenticated=False, mode=mode)
        assert client.get(f"{API}/meta").json()["mode"] == mode


DATASOURCE = {"id": "ds", "type": "sqlite", "connection": {"database": "ds.db"}}


def test_meta_start_says_choose_for_an_empty_project(stub):
    client, _ = stub(CheckReport(), authenticated=False, mode="your_data", datasources=[])

    assert client.get(f"{API}/meta").json()["start"] == "choose"


def test_meta_start_hides_an_empty_project_from_anyone_but_an_admin(stub):
    # No identity (api_keys without a key), and a caller who is not an admin: neither learns the project is empty.
    client, _ = stub(CheckReport(), authenticated=False, mode="your_data", datasources=[],
                     api={"auth": "api_keys"})
    assert client.get(f"{API}/meta").json()["start"] is None
    client, _ = stub(CheckReport(), authenticated=False, mode="your_data", datasources=[],
                     api={"auth": "static", "static_role": "analyst"})
    assert client.get(f"{API}/meta").json()["start"] is None


def test_meta_start_is_null_once_the_project_has_a_datasource(stub):
    for mode in ("demo", "your_data"):
        client, _ = stub(CheckReport(), authenticated=False, mode=mode, datasources=[DATASOURCE])
        assert client.get(f"{API}/meta").json()["start"] is None


def test_meta_start_says_hosted_on_the_hosted_demo(stub):
    client, _ = stub(CheckReport(), authenticated=False, mode="hosted", datasources=[DATASOURCE])

    assert client.get(f"{API}/meta").json()["start"] == "hosted"


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


def _caller(client, **kw):
    return client.get(f"{API}/meta", **kw).json().get("caller")


def test_meta_without_a_caller_names_no_caller(stub):
    client, _ = stub(CheckReport(), authenticated=False, api={"auth": "api_keys"})

    assert "caller" not in client.get(f"{API}/meta").json()


def test_meta_names_the_static_caller_and_whether_it_is_an_admin(stub):
    for role, admin in (("admin", True), ("analyst", False)):
        client, _ = stub(CheckReport(), api={"auth": "static", "static_role": role})
        assert _caller(client) == {"user_id": "static", "admin": admin}


def test_meta_names_the_api_key_holder(stub):
    client, engine = stub(CheckReport(), api={"auth": "api_keys"})
    token = "tok-" + "x" * 8  # built, not a real key
    engine.api_keys = SimpleNamespace(verify=lambda t: UserContext(user_id="ada", roles=["admin"]) if t == token else None)

    assert _caller(client, headers={"Authorization": f"Bearer {token}"}) == {"user_id": "ada", "admin": True}
    assert token not in client.get(f"{API}/meta", headers={"Authorization": f"Bearer {token}"}).text


def test_meta_names_the_proxy_caller(stub):
    api = {"auth": "proxy", "proxy": {"user_header": "X-User", "roles_header": "X-Roles", "tenant_header": None,
                                      "trusted_from": ["10.0.0.0/8"]}}
    _, _ = stub(CheckReport(), api=api)
    proxied = TestClient(app, client=("10.1.2.3", 5))

    assert _caller(proxied, headers={"X-User": "bo", "X-Roles": "viewer"}) == {"user_id": "bo", "admin": False}
    assert "caller" not in proxied.get(f"{API}/meta").json()  # no header, no caller


def test_meta_names_the_local_operator_as_an_admin(stub):
    _, _ = stub(CheckReport(), api={"auth": "none"})
    local = TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 5))

    assert _caller(local) == {"user_id": "local", "admin": True}


def test_meta_admin_is_the_answer_the_admin_routes_give(stub):
    policies = {"roles": {"boss": {"description": "r", "allow": ["*"]}, "analyst": {"description": "r", "allow": ["*"]}},
                "default_role": "analyst", "admin_roles": ["boss"]}
    for role in ("boss", "analyst"):
        client, _ = stub(CheckReport(), api={"auth": "static", "static_role": role}, policies=policies)
        route = client.get(f"{API}/evals/suites")  # an admin route: 200 for an admin, 403 for anyone else
        assert route.status_code == (200 if role == "boss" else 403)
        assert _caller(client)["admin"] is (route.status_code == 200)


def test_meta_local_operator_is_not_admin_when_no_admin_role_is_defined(stub):
    policies = {"roles": {"analyst": {"description": "r", "allow": ["*"]}}, "default_role": "analyst",
                "admin_roles": []}
    _, _ = stub(CheckReport(), api={"auth": "none"}, policies=policies)
    local = TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 5))

    assert _caller(local) == {"user_id": "local", "admin": False}
    assert local.get(f"{API}/evals/suites").status_code == 403


def test_meta_never_carries_another_users_data(stub):
    client, engine = stub(CheckReport(), api={"auth": "static", "static_role": "analyst"})

    body = client.get(f"{API}/meta").json()

    assert set(body["caller"]) <= {"user_id", "admin"}
    assert "roles" not in body["caller"] and "tenant_id" not in body["caller"]


def test_about_is_the_engines_description(api_client):
    client, engine = api_client()
    engine.about = lambda: {"version": "1.2.3", "steps": []}
    assert client.get(f"{API}/about").json() == {"version": "1.2.3", "steps": []}


def test_the_ready_check_is_kept_on_the_app_not_in_the_module(stub):
    client, engine = stub(CheckReport(datasources=[up()], llm={}, index="ok"))
    assert not hasattr(health, "_checked")
    client.get(f"{API}/ready")
    client.get(f"{API}/ready")
    assert engine.check.calls == 1 and app.state.ready_cache is not None

    health.forget_ready(app)
    client.get(f"{API}/ready")
    assert engine.check.calls == 2
