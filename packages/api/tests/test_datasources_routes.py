"""The datasources routes: reads pass the caller to the facade; Connect's admin
routes are covered in test_connect_routes.py. A stub engine records the calls."""
import pytest
from dashboard_stubs import Recorder

from nl2sql import DatasourceInfo, DatasourceNotFound, InvalidArgument, UserContext
from nl2sql_api import identity
from nl2sql_api.main import app

API = "/api/v1"
ADMIN = UserContext(roles=["admin"])


@pytest.fixture
def stub(api_client):
    client, engine = api_client()
    engine.datasources = Recorder()
    return client, engine.datasources


def info(datasource_id="chinook", questions=("How many tracks?",)):
    return DatasourceInfo(id=datasource_id, type="sqlite", description="Music", status="ready",
                          capabilities=["sql"], connection={"database": "chinook.db"},
                          sample_questions=list(questions))


def _not_found(*args, **kwargs):
    raise DatasourceNotFound("Unknown datasource ID: nope")


def test_datasources_list_has_sample_questions_in_scope(stub):
    client, datasources = stub
    datasources.returns["list"] = [info()]

    response = client.get(f"{API}/datasources")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [d["id"] for d in body] == ["chinook"] and body[0]["sample_questions"] == ["How many tracks?"]
    assert datasources.called("list") == [((), {"user_context": ADMIN})]


def test_get_a_datasource_passes_the_caller_and_404s_the_unknown(stub):
    client, datasources = stub
    datasources.returns["get"] = lambda datasource_id, **kwargs: (
        info(datasource_id) if datasource_id == "chinook" else _not_found())

    assert client.get(f"{API}/datasources/chinook").json()["id"] == "chinook"
    assert datasources.called("get") == [(("chinook",), {"user_context": ADMIN})]
    missing = client.get(f"{API}/datasources/nope")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"


def test_schema_route_hides_denied_tables(stub):
    client, datasources = stub
    seen = {}

    def schema(datasource_id, *, user_context):
        seen["caller"] = user_context
        return {"datasource_id": datasource_id, "tables": [{"name": "Track"}]}  # the facade left Invoice out

    datasources.returns["schema"] = schema

    body = client.get(f"{API}/datasources/chinook/schema").json()

    assert [t["name"] for t in body["tables"]] == ["Track"] and seen["caller"] == ADMIN


def test_diagram_route_is_mermaid_text_for_the_caller(stub):
    client, datasources = stub
    datasources.returns["diagram"] = "erDiagram\n  Track {\n  }\n"

    response = client.get(f"{API}/datasources/chinook/diagram", params=[("tables", "Track"), ("tables", "Album")])

    assert response.status_code == 200 and response.headers["content-type"].startswith("text/plain")
    assert response.text.startswith("erDiagram")
    assert datasources.called("diagram") == [(("chinook",), {"user_context": ADMIN, "tables": ["Track", "Album"]})]
    client.get(f"{API}/datasources/chinook/diagram")
    assert datasources.called("diagram")[-1][1]["tables"] is None


def test_diagram_of_tables_the_caller_cannot_see_is_a_422(stub):
    client, datasources = stub
    datasources.returns["diagram"] = InvalidArgument("No table of this datasource matches: Nope.")

    response = client.get(f"{API}/datasources/chinook/diagram", params={"tables": "Nope"})

    assert response.status_code == 422 and response.json()["error"]["code"] == "INVALID_ARGUMENT"


def test_reads_need_a_caller(api_client):
    client, engine = api_client(authenticated=False, api={"auth": "api_keys"})
    engine.datasources = Recorder()
    app.dependency_overrides.pop(identity.caller, None)

    assert client.get(f"{API}/datasources").status_code == 401
    assert client.get(f"{API}/datasources/chinook/schema").status_code == 401
    assert engine.datasources.calls == []


@pytest.mark.parametrize("path", ["schema", "diagram"])
def test_schema_and_diagram_check_the_datasource_first(stub, path):
    """An unknown or hidden datasource is a 404 before the facade reads anything (it would answer with no tables)."""
    client, datasources = stub
    datasources.returns["get"] = _not_found

    response = client.get(f"{API}/datasources/nope/{path}")

    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"
    assert datasources.called("get") == [(("nope",), {"user_context": ADMIN})]
    assert datasources.called(path) == []
