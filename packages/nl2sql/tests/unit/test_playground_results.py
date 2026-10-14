"""The playground serves a run's saved result by request id, as nl2sql-api does.

The hosted Space runs the playground, not nl2sql-api, so the same two reads
are here: ``GET /api/query/{request_id}/results/{result_id}`` for a page and
``.../{result_id}.csv`` for the whole result. Both call the facade, never the
engine context. The store holds ``run-1`` of tenant ``t1`` (45 rows) and a run
of tenant ``t2``.
"""
import csv
import io
from types import SimpleNamespace

import polars as pl
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from nl2sql import NL2SQL, ResultManifest, ResultSummary  # noqa: E402
from nl2sql.api.query_api import QueryAPI  # noqa: E402
from nl2sql.cli.demo.playground.app import build_app  # noqa: E402
from nl2sql.cli.demo.playground.hosted import Hosted  # noqa: E402
from nl2sql.execution.artifacts import ArtifactStore, ArtifactStoreConfig  # noqa: E402

TEMPLATE = "<tenant_id>/<request_id>/<sub_query_id>.parquet"
ROWS = [{"id": i, "name": f"n{i}"} for i in range(45)]


class _Engine(NL2SQL):
    def __init__(self, query):
        self.query = query

    def list_datasources(self):
        return ["chinook"]


def _save(store, tenant, request, rows):
    ref = store.write_lazy(pl.LazyFrame(rows), store.run_metadata(tenant, request, "result_combine_g1"))
    store.write_manifest(tenant, request, ResultManifest(request_id=request, results=[
        ResultSummary(node_id="combine_g1", columns=ref.columns, row_count=ref.row_count)]))


@pytest.fixture(params=["local", "hosted"])
def client(request, tmp_path):
    store = ArtifactStore(ArtifactStoreConfig(base_uri=str(tmp_path), path_template=TEMPLATE))
    _save(store, "t1", "run-1", ROWS)
    _save(store, "t2", "theirs", [{"id": 1, "name": "secret"}])
    engine = _Engine(QueryAPI(SimpleNamespace(tenant_id="t1", artifact_store=store)))
    hosted = Hosted(enabled=True) if request.param == "hosted" else None
    return TestClient(build_app(engine, questions=[], roles=["admin"], mode=request.param,
                                dataset="chinook", hosted=hosted))


def test_a_page_of_the_result(client):
    response = client.get("/api/query/run-1/results/combine_g1", params={"offset": 20, "limit": 20})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rows"] == [[i, f"n{i}"] for i in range(20, 40)]
    assert (body["total_rows"], body["next_offset"]) == (45, 40)


def test_the_page_size_is_capped_and_bad_values_refused(client):
    assert client.get("/api/query/run-1/results/combine_g1", params={"limit": 5000}).json()["limit"] == 1000
    assert client.get("/api/query/run-1/results/combine_g1", params={"offset": -1}).status_code == 422
    assert client.get("/api/query/run-1/results/combine_g1", params={"limit": 0}).status_code == 422


def test_the_csv_is_the_whole_result(client):
    response = client.get("/api/query/run-1/results/combine_g1.csv")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="run-1_combine_g1.csv"'
    lines = list(csv.reader(io.StringIO(response.text)))
    assert lines == [["id", "name"]] + [[str(r["id"]), r["name"]] for r in ROWS]


@pytest.mark.parametrize("path", [
    "/api/query/run-2/results/combine_g1",
    "/api/query/run-1/results/combine_nope",
    "/api/query/theirs/results/combine_g1",
    "/api/query/theirs/results/combine_g1.csv",
    "/api/query/..%2Ft2%2Ftheirs/results/combine_g1",
    "/api/query/run-1/results/..%2F..%2Ft2%2Ftheirs%2Fresult_combine_g1.csv",
])
def test_an_unknown_foreign_or_crafted_id_is_404(client, path):
    response = client.get(path)

    assert response.status_code == 404
    assert "secret" not in response.text
