"""GET /query/{request_id}/results/{result_id}: a page of a run's saved result, or all of it as CSV.

The routes call the facade's ``engine.results.page`` and ``engine.results.csv``,
which read the run's parquet artifact lazily. The engine here is the real
facade over a local artifact store holding one run, ``run-1`` of tenant
``t1``, with 45 rows in ``combine_g1``, asked by ``ann``; ``bob`` and tenant ``t2`` hold runs of their own. A run is
readable only by the caller who asked it: anyone else's trace id is a 404, as a missing run is.
"""
import csv
import io
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi.testclient import TestClient

from nl2sql import NL2SQL, UserContext, ResultManifest, ResultSummary
from nl2sql.execution.artifacts.results import ResultOwner
from nl2sql.execution.artifacts import ArtifactStore
from nl2sql.storage.local import LocalResults
from nl2sql.testing.config import make_config
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.main import app

TEMPLATE = "<tenant_id>/<request_id>/<sub_query_id>.parquet"
ROWS = [{"id": i, "name": f"n{i}"} for i in range(45)]
ANN = UserContext(user_id="ann", roles=["admin"])


def _save(store, tenant, request, rows, user_id="ann", asker_tenant="t1"):
    ref = store.write_lazy(pl.LazyFrame(rows), store.run_metadata(tenant, request, "result_combine_g1"))
    owner = ResultOwner(tenant_id=asker_tenant, user_id=user_id)
    store.write_manifest(tenant, request, ResultManifest(request_id=request, owner=owner, results=[
        ResultSummary(node_id="combine_g1", columns=ref.columns, row_count=ref.row_count)]))


@pytest.fixture
def client(tmp_path):
    store = ArtifactStore(LocalResults(str(tmp_path)), path_template=TEMPLATE)
    _save(store, "t1", "run-1", ROWS)
    _save(store, "t2", "theirs", [{"id": 1, "name": "secret"}])
    _save(store, "t1", "bobs", [{"id": 1, "name": "secret"}], user_id="bob")
    _save(store, "t1", "acmes", [{"id": 1, "name": "secret"}], asker_tenant="acme")
    engine = NL2SQL.__new__(NL2SQL)
    # The API reads its role settings from the engine's config.
    engine._runtime = SimpleNamespace(config=make_config(tmp_path / "project"), closed=False,
                                      tenant_id="t1", artifact_store=store)
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[identity.caller] = lambda: ANN
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
    "/api/v1/query/bobs/results/combine_g1",
    "/api/v1/query/bobs/results/combine_g1.csv",
    "/api/v1/query/acmes/results/combine_g1",
    "/api/v1/query/acmes/results/combine_g1.csv",
    "/api/v1/query/..%2Ft2%2Ftheirs/results/combine_g1",
    "/api/v1/query/run-1/results/..%2F..%2Ft2%2Ftheirs%2Fresult_combine_g1",
])
def test_an_unknown_foreign_or_crafted_id_is_404(client, path):
    response = client.get(path)

    assert response.status_code == 404
    assert "secret" not in response.text


def _ran(monkeypatch, client, decision):
    """POSTs a question that runs the real aggregator and answer node, with a hook at answer.after deciding."""
    from langgraph.graph import END, StateGraph
    from unittest.mock import MagicMock

    from nl2sql.execution.dag import ExecutionDAG, LogicalEdge, LogicalNode
    from nl2sql.hooks import HookBinding, HookRunner
    from nl2sql.pipeline.nodes.aggregator.node import EngineAggregatorNode
    from nl2sql.pipeline.nodes.answer_synthesizer.node import AnswerSynthesizerNode
    from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AggregatedResponse
    from nl2sql.pipeline.state import GraphState
    from nl2sql.testing.runtime import fake_runtime

    def graph(ctx, execute=True, **_):
        def executed(state):
            ref = ctx.artifact_store.write_lazy(pl.LazyFrame(ROWS),
                                                ctx.artifact_store.run_metadata("t1", state.trace_id, "sq1"))
            dag = ExecutionDAG(nodes=[LogicalNode(node_id="sq1", kind="scan", inputs=[]),
                                      LogicalNode(node_id="combine_g1", kind="combine", inputs=["sq1"],
                                                  attributes={"operation": "standalone", "join_keys": []})],
                               edges=[LogicalEdge(edge_id="e1", from_id="sq1", to_id="combine_g1")])
            return {"execution_dag": dag, "artifact_refs": {"sq1": ref}}

        answer = AnswerSynthesizerNode(ctx)
        answer.chain = SimpleNamespace(invoke=lambda inputs: AggregatedResponse(
            summary="45.", format_type="text", content="45 rows.", warnings=[]))
        builder = StateGraph(GraphState)
        builder.add_node("executed", executed)
        builder.add_node("aggregator", EngineAggregatorNode(ctx))
        builder.add_node("answer_synthesizer", answer)
        builder.set_entry_point("executed")
        builder.add_edge("executed", "aggregator")
        builder.add_edge("aggregator", "answer_synthesizer")
        builder.add_edge("answer_synthesizer", END)
        return builder.compile()

    monkeypatch.setattr("nl2sql.pipeline.runtime.build_graph", graph)
    engine = app.dependency_overrides[get_engine]()
    hook = HookBinding(name="review", hook=decision, points=frozenset({"answer.after"}))
    engine._runtime = fake_runtime(settings={"tenant_id": "t1"}, artifact_store=engine._runtime.artifact_store,
                                   hooks=HookRunner([hook]), llm_registry=MagicMock(), closed=False)
    response = client.post("/api/v1/query", json={"natural_language": "How many?"})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("suffix", ["", ".csv"])
def test_a_run_a_hook_denied_is_404_by_its_trace_id(monkeypatch, client, suffix):
    from nl2sql.hooks import Deny

    body = _ran(monkeypatch, client, lambda ctx: Deny(reason="Reviewed first"))

    assert body["errors"][0]["error_code"] == "HOOK_DENIED"
    response = client.get(f"/api/v1/query/{body['trace_id']}/results/combine_g1{suffix}")
    assert response.status_code == 404
    assert "n1" not in response.text


def test_a_run_a_hook_allowed_is_readable_by_its_trace_id(monkeypatch, client):
    from nl2sql.hooks import Allow

    body = _ran(monkeypatch, client, lambda ctx: Allow())

    assert client.get(f"/api/v1/query/{body['trace_id']}/results/combine_g1").json()["total_rows"] == 45


@pytest.mark.parametrize("suffix", ["", ".csv"])
def test_a_run_is_readable_only_by_the_caller_who_asked_it(monkeypatch, client, suffix):
    from nl2sql.hooks import Allow

    body = _ran(monkeypatch, client, lambda ctx: Allow())
    path = f"/api/v1/query/{body['trace_id']}/results/combine_g1{suffix}"
    assert client.get(path).status_code == 200

    for other in (UserContext(user_id="bob", roles=["admin"]), UserContext(roles=["admin"]),
                  UserContext(user_id="ann", tenant_id="t2", roles=["admin"])):
        app.dependency_overrides[identity.caller] = lambda other=other: other
        response = client.get(path)
        assert response.status_code == 404, other
        assert "n1" not in response.text


@pytest.mark.parametrize("suffix", ["", ".csv"])
def test_an_anonymous_run_is_readable_only_with_the_same_roles(monkeypatch, client, suffix):
    """REST callers carry roles and no user id (``nl2sql_api.identity``), so a run is bound to its tenant and roles:
    a viewer holding an admin run's trace id must not read rows its own policies would hide."""
    from nl2sql.hooks import Allow

    app.dependency_overrides[identity.caller] = lambda: UserContext(roles=["admin"])
    body = _ran(monkeypatch, client, lambda ctx: Allow())
    path = f"/api/v1/query/{body['trace_id']}/results/combine_g1{suffix}"
    assert client.get(path).status_code == 200

    for other in (UserContext(roles=["viewer"]), UserContext(roles=["admin", "viewer"])):
        app.dependency_overrides[identity.caller] = lambda other=other: other
        response = client.get(path)
        assert response.status_code == 404, other
        assert "n1" not in response.text


def test_the_routes_need_a_role_like_query_does(client):
    app.dependency_overrides.pop(identity.caller)

    # api.auth is none here and the client's Host is not localhost: not the local operator, so no identity.
    assert client.get("/api/v1/query/run-1/results/combine_g1").status_code == 403
    assert client.get("/api/v1/query/run-1/results/combine_g1.csv").status_code == 403


def test_the_query_response_carries_the_result_manifest_not_rows(api_client):
    from nl2sql import QueryResult

    summary = ResultSummary(node_id="combine_g1", columns=["id", "name"], row_count=45, truncated=True)
    client, _ = api_client(QueryResult(trace_id="run-1", result_manifest=[summary]))

    body = client.post("/api/v1/query", json={"natural_language": "q"}).json()

    assert body["result_manifest"] == [
        {"node_id": "combine_g1", "columns": ["id", "name"], "row_count": 45, "truncated": True, "column_kinds": {}}]
