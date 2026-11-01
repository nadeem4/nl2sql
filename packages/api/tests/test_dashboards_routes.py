"""The dashboards and workspaces routes (docs/architecture/wave_d.md, row A2b): thin over ``engine.dashboards`` and
``engine.workspaces``, each handler passing the caller. A stub engine records the calls; the errors and the ownership
rules are the real engine's, over a real one at the bottom."""
import threading
import time

import pytest
from dashboard_stubs import AT, Recorder, dashboard, run, view, workspace
from fastapi.testclient import TestClient

from nl2sql import (DashboardBusy, DashboardInfo, DashboardNotFound, NotIndexed, NotPinnable, NoTiles,
                    ScheduleTooFrequent, TileProgress, UserContext, WorkspaceNotEmpty, WorkspaceNotFound)
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.main import app

API = "/api/v1"
ADMIN = UserContext(roles=["admin"])  # what the conftest's api_client resolves every request to


@pytest.fixture
def stub(api_client):
    client, engine = api_client()
    engine.dashboards, engine.workspaces = Recorder(), Recorder()
    return client, engine.dashboards, engine.workspaces


def wait_for(client, job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"{API}/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        assert time.monotonic() < deadline, "the job did not finish"
        time.sleep(0.01)


def error_code(response):
    return response.json()["error"]["code"]


# ---- the generate job

def test_generate_is_a_job_with_tile_progress(stub):
    client, dashboards, _ = stub
    release = threading.Event()

    def generate(datasource_id, **kwargs):
        kwargs["on_tile"](TileProgress(title="Revenue", status="ok", done=1, total=2))
        release.wait(5)
        kwargs["on_tile"](TileProgress(title="Countries", status="ok", done=2, total=2))
        return dashboard(), run("generate")

    dashboards.returns["generate"] = generate
    response = client.post(f"{API}/jobs/dashboard", json={"datasource_id": "chinook", "title": "Sales",
                                                          "workspace_id": "w1", "max_tiles": 4})
    assert response.status_code == 202
    job = response.json()
    assert job["kind"] == "dashboard" and job["status"] in ("queued", "running")
    assert job["params"] == {"datasource_id": "chinook", "title": "Sales", "workspace_id": "w1", "max_tiles": 4}
    deadline = time.monotonic() + 5
    while not client.get(f"{API}/jobs/{job['id']}").json()["progress"]:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    release.set()
    done = wait_for(client, job["id"])
    assert done["status"] == "succeeded"
    assert [(p["step"], p["done"], p["total"]) for p in done["progress"]] == [("tile 1 of 2", 1, 2),
                                                                             ("tile 2 of 2", 2, 2)]
    assert done["result"]["dashboard"]["id"] == "d1" and done["result"]["run"]["trigger"] == "generate"
    (args, kwargs), = dashboards.called("generate")
    assert args == ("chinook",)
    assert kwargs["user_context"] == ADMIN and kwargs["title"] == "Sales" and kwargs["workspace_id"] == "w1"
    assert kwargs["max_tiles"] == 4 and kwargs["cancellation_token"] is not None


def test_generate_passes_nothing_the_body_left_out(stub):
    client, dashboards, _ = stub
    dashboards.returns["generate"] = lambda *a, **k: (dashboard(), run("generate"))
    job = client.post(f"{API}/jobs/dashboard", json={"datasource_id": "chinook"}).json()
    wait_for(client, job["id"])
    (_, kwargs), = dashboards.called("generate")
    assert (kwargs["title"], kwargs["workspace_id"], kwargs["max_tiles"]) == (None, None, None)


def test_generate_failure_is_the_jobs_error(stub):
    client, dashboards, _ = stub
    dashboards.returns["generate"] = NoTiles("None of the 3 starter charts answered.")
    job = wait_for(client, client.post(f"{API}/jobs/dashboard", json={"datasource_id": "chinook"}).json()["id"])
    assert job["status"] == "failed" and job["error"]["code"] == "NO_TILES"


def test_generate_body_is_strict(stub):
    client, _, _ = stub
    assert client.post(f"{API}/jobs/dashboard", json={"datasource_id": "chinook", "extra": 1}).status_code == 422
    assert client.post(f"{API}/jobs/dashboard", json={}).status_code == 422


def test_generate_is_for_any_caller(api_client):
    client, engine = api_client(authenticated=False)
    engine.dashboards = Recorder()
    engine.dashboards.returns["generate"] = lambda *a, **k: (dashboard(), run("generate"))
    app.dependency_overrides[identity.caller] = lambda: UserContext(user_id="ann", roles=["analyst"])
    assert client.post(f"{API}/jobs/dashboard", json={"datasource_id": "chinook"}).status_code == 202


# ---- reading

def test_list_the_callers_dashboards(stub):
    client, dashboards, _ = stub
    dashboards.returns["list"] = [DashboardInfo(id="d1", title="Sales overview", workspace_id="w1", status="saved",
                                                tiles=2, refreshed_at=AT, updated_at=AT)]
    body = client.get(f"{API}/dashboards", params={"workspace_id": "w1"}).json()
    assert body[0]["id"] == "d1" and body[0]["tiles"] == 2
    assert dashboards.called("list") == [((), {"user_context": ADMIN, "workspace_id": "w1", "status": "saved"})]
    client.get(f"{API}/dashboards", params={"status": "draft"})
    assert dashboards.called("list")[-1][1] == {"user_context": ADMIN, "workspace_id": None, "status": "draft"}
    assert client.get(f"{API}/dashboards", params={"status": "mine"}).status_code == 422


def test_open_serves_stored_results_and_runs_nothing(stub):
    client, dashboards, _ = stub
    dashboards.returns["open"] = view()
    body = client.get(f"{API}/dashboards/d1").json()
    assert [r["status"] for r in body["runs"]] == ["ok", "needs_fixing", "denied", "needs_approval"]
    assert body["due"] == ["t1"] and body["refreshed_at"] is not None
    assert body["runs"][0]["chart"]["data"]["rows"][0] == ["2026-01", 120.5]
    assert dashboards.called("open") == [(("d1",), {"user_context": ADMIN, "refresh_due": False})]
    assert not dashboards.called("refresh") and client.get(f"{API}/jobs").json() == []


def test_open_another_users_dashboard_is_404(stub):
    client, dashboards, _ = stub
    dashboards.returns["open"] = DashboardNotFound("No dashboard 'd9'.")
    response = client.get(f"{API}/dashboards/d9")
    assert response.status_code == 404 and error_code(response) == "NOT_FOUND"


# ---- refresh

def test_refresh_with_filters(stub):
    client, dashboards, _ = stub
    dashboards.returns["refresh"] = run("manual", filters={"f1": ["Canada"]})
    body = client.post(f"{API}/dashboards/d1/refresh", json={
        "tile_id": "t1", "filters": {"f1": ["Canada"]}, "approval_ids": ["req-1"]}).json()
    assert body["trigger"] == "manual" and body["filters"] == {"f1": ["Canada"]}
    assert dashboards.called("refresh") == [(("d1",), {"user_context": ADMIN, "tile_id": "t1",
                                                       "filters": {"f1": ["Canada"]}, "approvals": ["req-1"]})]


def test_refresh_with_no_body_refreshes_everything(stub):
    client, dashboards, _ = stub
    dashboards.returns["refresh"] = run("manual")
    assert client.post(f"{API}/dashboards/d1/refresh").status_code == 200
    assert dashboards.called("refresh")[0][1] == {"user_context": ADMIN, "tile_id": None, "filters": None,
                                                  "approvals": []}


def test_busy_dashboard_is_409(stub):
    client, dashboards, _ = stub
    dashboards.returns["refresh"] = DashboardBusy("Dashboard 'd1' is being refreshed.")
    response = client.post(f"{API}/dashboards/d1/refresh", json={})
    assert response.status_code == 409 and error_code(response) == "CONFLICT"


def test_filter_values(stub):
    client, dashboards, _ = stub
    dashboards.returns["filter_values"] = ["Canada", "France"]
    assert client.get(f"{API}/dashboards/d1/filters/f1/values", params={"limit": 25}).json() == ["Canada", "France"]
    assert dashboards.called("filter_values") == [(("d1", "f1"), {"user_context": ADMIN, "limit": 25})]
    client.get(f"{API}/dashboards/d1/filters/f1/values")
    assert dashboards.called("filter_values")[-1][1]["limit"] == 100  # the facade's default


# ---- edits

def test_edit_routes_call_the_named_methods(stub):
    client, dashboards, _ = stub
    for name in ("rename", "rename_tile", "set_tile_chart"):
        dashboards.returns[name] = dashboard()
    assert client.put(f"{API}/dashboards/d1/title", json={"title": "Q1"}).json()["id"] == "d1"
    assert client.put(f"{API}/dashboards/d1/tiles/t1/title", json={"title": "Sales"}).status_code == 200
    assert client.put(f"{API}/dashboards/d1/tiles/t1/chart", json={"chart": "line"}).status_code == 200
    assert dashboards.called("rename") == [(("d1", "Q1"), {"user_context": ADMIN})]
    assert dashboards.called("rename_tile") == [(("d1", "t1", "Sales"), {"user_context": ADMIN})]
    assert dashboards.called("set_tile_chart") == [(("d1", "t1", "line"), {"user_context": ADMIN})]
    assert client.put(f"{API}/dashboards/d1/tiles/t1/chart", json={"chart": "pie"}).status_code == 422
    assert client.put(f"{API}/dashboards/d1/title", json={"title": "x", "extra": 1}).status_code == 422


def test_arrange_layout(stub):
    client, dashboards, _ = stub
    dashboards.returns["arrange"] = dashboard()
    body = {"arrangements": [{"tile_id": "t1", "x": 0, "y": 0, "w": 4, "h": 2},
                             {"tile_id": "t2", "x": 0, "y": 2, "w": 2, "h": 2}]}
    assert client.put(f"{API}/dashboards/d1/layout", json=body).status_code == 200
    (args, kwargs), = dashboards.called("arrange")
    assert args[0] == "d1" and kwargs == {"user_context": ADMIN}
    assert [(a.tile_id, a.x, a.y, a.w, a.h) for a in args[1]] == [("t1", 0, 0, 4, 2), ("t2", 0, 2, 2, 2)]
    off_grid = {"arrangements": [{"tile_id": "t1", "x": 3, "y": 0, "w": 2, "h": 2}]}
    assert client.put(f"{API}/dashboards/d1/layout", json=off_grid).status_code == 422


def test_remove_a_tile(stub):
    client, dashboards, _ = stub
    dashboards.returns["remove_tile"] = dashboard()
    assert client.delete(f"{API}/dashboards/d1/tiles/t2").json()["id"] == "d1"
    assert dashboards.called("remove_tile") == [(("d1", "t2"), {"user_context": ADMIN})]


def test_set_filters_and_schedule(stub):
    client, dashboards, _ = stub
    dashboards.returns["set_filters"] = dashboards.returns["set_schedule"] = dashboard()
    filters = [{"id": "f1", "label": "Country", "column": "chinook.main.customers.country", "kind": "text",
                "op": "in"}]
    assert client.put(f"{API}/dashboards/d1/filters", json={"filters": filters}).status_code == 200
    (args, kwargs), = dashboards.called("set_filters")
    assert args[0] == "d1" and args[1][0].id == "f1" and args[1][0].op == "in" and kwargs == {"user_context": ADMIN}
    assert client.put(f"{API}/dashboards/d1/schedule", json={"every_minutes": 60}).status_code == 200
    assert client.put(f"{API}/dashboards/d1/schedule", json={"every_minutes": None}).status_code == 200
    assert [a for a, _ in dashboards.called("set_schedule")] == [("d1", 60), ("d1", None)]
    assert client.put(f"{API}/dashboards/d1/schedule", json={}).status_code == 422  # null is said, not left out


def test_schedule_too_frequent_is_422(stub):
    client, dashboards, _ = stub
    dashboards.returns["set_schedule"] = ScheduleTooFrequent("A schedule must be 15 minutes or more.")
    response = client.put(f"{API}/dashboards/d1/schedule", json={"every_minutes": 1})
    assert response.status_code == 422 and error_code(response) == "SCHEDULE_TOO_FREQUENT"


# ---- pin, move, delete

def test_pin_a_draft_whole_into_a_workspace(stub):
    client, dashboards, _ = stub
    dashboards.returns["pin"] = dashboard()
    assert client.post(f"{API}/dashboards/d1/pin", json={"workspace_id": "w1", "title": "Sales"}).json()[
        "status"] == "saved"
    assert dashboards.called("pin") == [(("d1",), {"user_context": ADMIN, "workspace_id": "w1", "title": "Sales"})]
    client.post(f"{API}/dashboards/d1/pin")
    assert dashboards.called("pin")[-1][1] == {"user_context": ADMIN, "workspace_id": None, "title": None}


def test_pin_a_saved_dashboard_is_422(stub):
    client, dashboards, _ = stub
    dashboards.returns["pin"] = NotPinnable("Dashboard 'd1' is already saved.")
    response = client.post(f"{API}/dashboards/d1/pin", json={})
    assert response.status_code == 422 and error_code(response) == "NOT_PINNABLE"


def test_move_to_another_workspace(stub):
    client, dashboards, _ = stub
    dashboards.returns["move"] = dashboard()
    assert client.post(f"{API}/dashboards/d1/move", json={"workspace_id": "w2"}).status_code == 200
    assert dashboards.called("move") == [(("d1", "w2"), {"user_context": ADMIN})]
    dashboards.returns["move"] = WorkspaceNotFound("No workspace 'w9'.")
    assert client.post(f"{API}/dashboards/d1/move", json={"workspace_id": "w9"}).status_code == 404
    assert client.post(f"{API}/dashboards/d1/move", json={}).status_code == 422


def test_delete_a_dashboard(stub):
    client, dashboards, _ = stub
    response = client.delete(f"{API}/dashboards/d1")
    assert response.status_code == 204 and response.content == b""
    assert dashboards.called("delete") == [(("d1",), {"user_context": ADMIN})]


# ---- workspaces

def test_workspace_create_rename_delete(stub):
    client, _, workspaces = stub
    workspaces.returns["list"] = [workspace()]
    workspaces.returns["create"] = workspace("Sales")
    workspaces.returns["rename"] = workspace("Revenue")
    assert client.get(f"{API}/workspaces").json()[0]["name"] == "General"
    created = client.post(f"{API}/workspaces", json={"name": "Sales"})
    assert created.status_code == 201 and created.json()["name"] == "Sales"
    assert client.put(f"{API}/workspaces/w1/name", json={"name": "Revenue"}).json()["name"] == "Revenue"
    deleted = client.delete(f"{API}/workspaces/w1")
    assert deleted.status_code == 204 and deleted.content == b""
    assert workspaces.called("list") == [((), {"user_context": ADMIN})]
    assert workspaces.called("create") == [(("Sales",), {"user_context": ADMIN})]
    assert workspaces.called("rename") == [(("w1", "Revenue"), {"user_context": ADMIN})]
    assert workspaces.called("delete") == [(("w1",), {"user_context": ADMIN})]


def test_delete_a_non_empty_workspace_is_422(stub):
    client, _, workspaces = stub
    workspaces.returns["delete"] = WorkspaceNotEmpty("Workspace 'General' still holds dashboards.")
    response = client.delete(f"{API}/workspaces/w1")
    assert response.status_code == 422 and error_code(response) == "WORKSPACE_NOT_EMPTY"


# ---- starter questions

def test_starter_questions(stub):
    client, dashboards, _ = stub
    dashboards.returns["suggest_questions"] = ["How many customers are there?"]
    assert client.get(f"{API}/datasources/chinook/starter-questions", params={"n": 3}).json() == [
        "How many customers are there?"]
    assert dashboards.called("suggest_questions") == [(("chinook",), {"user_context": ADMIN, "n": 3})]
    client.get(f"{API}/datasources/chinook/starter-questions")
    assert dashboards.called("suggest_questions")[-1][1]["n"] == 5
    assert client.get(f"{API}/datasources/chinook/starter-questions", params={"n": 0}).status_code == 422


def test_starter_questions_need_a_caller(api_client):
    client, engine = api_client(authenticated=False, api={"auth": "api_keys"})
    engine.dashboards = Recorder()
    response = client.get(f"{API}/datasources/chinook/starter-questions")
    assert response.status_code == 401 and not engine.dashboards.calls


def test_starter_questions_for_an_unindexed_datasource_is_422(stub):
    client, dashboards, _ = stub
    dashboards.returns["suggest_questions"] = NotIndexed("'chinook' has no index yet.")
    response = client.get(f"{API}/datasources/chinook/starter-questions")
    assert response.status_code == 422 and error_code(response) == "NOT_INDEXED"


# ---- over a real engine: the store, ownership and the errors are the engine's own

@pytest.fixture
def real(tmp_path):
    from nl2sql import NL2SQL
    from nl2sql.testing.config import make_config

    engine = NL2SQL(make_config(tmp_path / "real"))
    who = {"user": UserContext(user_id="ann", tenant_id="t", roles=["analyst"])}
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[identity.caller] = lambda: who["user"]
    client = TestClient(app, raise_server_exceptions=False)
    yield client, engine, who
    app.dependency_overrides.clear()
    client.close()
    engine.close()


def test_other_users_dashboard_is_404(real):
    client, engine, who = real
    draft = engine.dashboards.create_draft(user_context=who["user"], title="Mine")
    assert client.get(f"{API}/dashboards/{draft.id}").status_code == 200
    who["user"] = UserContext(user_id="bob", tenant_id="t", roles=["analyst"])
    for method, path in (("get", ""), ("delete", ""), ("post", "/pin"), ("put", "/title")):
        extra = {"json": {"title": "x"}} if method == "put" else {}
        response = getattr(client, method)(f"{API}/dashboards/{draft.id}{path}", **extra)
        assert response.status_code == 404, (method, path)
    assert client.get(f"{API}/dashboards", params={"status": "draft"}).json() == []


def test_workspace_flow_over_the_real_engine(real):
    client, engine, who = real
    created = client.post(f"{API}/workspaces", json={"name": "Sales"}).json()
    assert client.put(f"{API}/workspaces/{created['id']}/name", json={"name": "Revenue"}).json()["name"] == "Revenue"
    draft = engine.dashboards.create_draft(user_context=who["user"], workspace_id=created["id"])
    listed = client.get(f"{API}/dashboards", params={"workspace_id": created["id"], "status": "draft"}).json()
    assert listed[0]["id"] == draft.id
    blocked = client.delete(f"{API}/workspaces/{created['id']}")
    assert blocked.status_code == 422 and error_code(blocked) == "WORKSPACE_NOT_EMPTY"
    assert client.delete(f"{API}/dashboards/{draft.id}").status_code == 204
    assert client.delete(f"{API}/workspaces/{created['id']}").status_code == 204
    assert client.put(f"{API}/workspaces/{created['id']}/name", json={"name": "Gone"}).status_code == 404


def test_pin_an_empty_draft_and_a_too_frequent_schedule_over_the_real_engine(real):
    client, engine, who = real
    draft = engine.dashboards.create_draft(user_context=who["user"])
    pin = client.post(f"{API}/dashboards/{draft.id}/pin", json={})
    assert pin.status_code == 422 and error_code(pin) == "NOT_PINNABLE"
    schedule = client.put(f"{API}/dashboards/{draft.id}/schedule", json={"every_minutes": 1})
    assert schedule.status_code == 422 and error_code(schedule) == "SCHEDULE_TOO_FREQUENT"


def test_starter_questions_for_an_unindexed_datasource_over_the_real_engine(real):
    client, _, _ = real
    response = client.get(f"{API}/datasources/chinook/starter-questions")
    assert response.status_code == 422 and error_code(response) == "NOT_INDEXED"


def test_refresh_with_an_unknown_approval_id_is_404(stub):
    from nl2sql import ApprovalNotFound

    client, dashboards, _ = stub
    dashboards.returns["refresh"] = ApprovalNotFound("There is no approval request 'nope'.")
    response = client.post(f"{API}/dashboards/d1/refresh", json={"approval_ids": ["nope"]})
    assert response.status_code == 404 and error_code(response) == "NOT_FOUND"


def test_the_dashboard_job_refuses_zero_max_tiles(stub):
    client, _, _ = stub
    response = client.post(f"{API}/jobs/dashboard", json={"datasource_id": "chinook", "max_tiles": 0})
    assert response.status_code == 422


def test_chart_body_matches_the_engines_chart_choices():
    from typing import get_args

    from nl2sql import Tile
    from nl2sql_api.models.dashboards import ChartBody

    assert set(get_args(ChartBody.model_fields["chart"].annotation)) == set(
        get_args(Tile.model_fields["chart"].annotation))


def test_filter_values_does_not_list_a_409():
    from nl2sql_api.main import create_app

    responses = create_app().openapi()["paths"]["/api/v1/dashboards/{dashboard_id}/filters/{filter_id}/values"]["get"]["responses"]
    assert "409" not in responses and "404" in responses and "422" in responses
