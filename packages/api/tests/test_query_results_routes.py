"""GET /query/{request_id}/results/{result_id}: a page of a run's saved result, or all of it as CSV.

The routes call the facade's ``get_result_page`` and ``export_result_csv``,
which read the run's parquet artifact lazily. The engine here is the real
facade over a local artifact store holding one run, ``run-1`` of tenant
``t1``, with 45 rows in ``combine_g1``; tenant ``t2`` holds a run of its own.
"""
import csv
import io
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi.testclient import TestClient

from nl2sql import NL2SQL, UserContext, ResultManifest, ResultSummary
from nl2sql.api.query_api import QueryAPI
from nl2sql.execution.artifacts import ArtifactStore, ArtifactStoreConfig
from nl2sql_api.auth import get_user_context
from nl2sql_api.dependencies import get_engine
from nl2sql_api.main import app

TEMPLATE = "<tenant_id>/<request_id>/<sub_query_id>.parquet"
ROWS = [{"id": i, "name": f"n{i}"} for i in range(45)]


def _save(store, tenant, request, rows):
    ref = store.write_lazy(pl.LazyFrame(rows), store.run_metadata(tenant, request, "result_combine_g1"))
    store.write_manifest(tenant, request, ResultManifest(request_id=request, results=[
        ResultSummary(node_id="combine_g1", columns=ref.columns, row_count=ref.row_count)]))


@pytest.fixture
def client(tmp_path):
    store = ArtifactStore(ArtifactStoreConfig(base_uri=str(tmp_path), path_template=TEMPLATE))
    _save(store, "t1", "run-1", ROWS)
    _save(store, "t2", "theirs", [{"id": 1, "name": "secret"}])
    engine = NL2SQL.__new__(NL2SQL)
    engine.query = QueryAPI(SimpleNamespace(tenant_id="t1", artifact_store=store))
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_user_context] = lambda: UserContext(roles=["admin"])
    with_client = TestClient(app)
    yield with_client
    app.dependency_overrides.clear()
    with_client.close()


def test_a_page_of_the_result(client):
    response = client.get("/api/v1/query/run-1/results/combine_g1", params={"offset": 40, "limit": 20})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["columns"] == ["id", "name"]
    assert body["rows"] == [[i, f"n{i}"] for i in range(40, 45)]
    assert (body["offset"], body["limit"], body["total_rows"], body["next_offset"]) == (40, 20, 45, None)


def test_the_first_page_by_default(client):
    body = client.get("/api/v1/query/run-1/results/combine_g1").json()

    assert body["offset"] == 0 and body["limit"] == 100
    assert len(body["rows"]) == 45 and body["next_offset"] is None


def test_a_page_past_the_end_is_empty(client):
    body = client.get("/api/v1/query/run-1/results/combine_g1", params={"offset": 45}).json()

    assert body["rows"] == [] and body["total_rows"] == 45


def test_the_page_size_is_capped(client):
    body = client.get("/api/v1/query/run-1/results/combine_g1", params={"limit": 5000}).json()

    assert body["limit"] == 1000


@pytest.mark.parametrize("params", [{"offset": -1}, {"limit": 0}, {"offset": "x"}])
def test_a_bad_offset_or_limit_is_422(client, params):
    assert client.get("/api/v1/query/run-1/results/combine_g1", params=params).status_code == 422


def test_the_csv_is_the_whole_result(client):
    response = client.get("/api/v1/query/run-1/results/combine_g1.csv")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="run-1_combine_g1.csv"' == response.headers["content-disposition"]
    lines = list(csv.reader(io.StringIO(response.text)))
    assert lines[0] == ["id", "name"]
    assert lines[1:] == [[str(r["id"]), r["name"]] for r in ROWS]


@pytest.mark.parametrize("path", [
    "/api/v1/query/run-2/results/combine_g1",
    "/api/v1/query/run-1/results/combine_nope",
    "/api/v1/query/theirs/results/combine_g1",
    "/api/v1/query/run-2/results/combine_g1.csv",
    "/api/v1/query/theirs/results/combine_g1.csv",
    "/api/v1/query/..%2Ft2%2Ftheirs/results/combine_g1",
    "/api/v1/query/run-1/results/..%2F..%2Ft2%2Ftheirs%2Fresult_combine_g1",
])
def test_an_unknown_foreign_or_crafted_id_is_404(client, path):
    response = client.get(path)

    assert response.status_code == 404
    assert "secret" not in response.text


def test_the_routes_need_a_role_like_query_does(client):
    app.dependency_overrides.pop(get_user_context)

    assert client.get("/api/v1/query/run-1/results/combine_g1").status_code == 401
    assert client.get("/api/v1/query/run-1/results/combine_g1.csv").status_code == 401


def test_the_query_response_carries_the_result_manifest_not_rows(api_client):
    from nl2sql import QueryResult

    summary = ResultSummary(node_id="combine_g1", columns=["id", "name"], row_count=45, truncated=True)
    client, _ = api_client(QueryResult(trace_id="run-1", result_manifest=[summary]))

    body = client.post("/api/v1/query", json={"natural_language": "q"}).json()

    assert body["result_manifest"] == [
        {"node_id": "combine_g1", "columns": ["id", "name"], "row_count": 45, "truncated": True}]
