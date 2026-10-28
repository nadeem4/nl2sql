"""A run's results, read back by request id: the manifest, a page, and a CSV export.

The aggregator saves each terminal result as ``result_<node_id>.parquet`` in
the run's folder and writes ``manifest.json`` beside them: which results the
run produced, with their columns, row counts and truncated flags, never the
rows. ``QueryResult.result_manifest`` carries the same entries. ``engine.results`` reads a
page of a result, or streams the whole of it as CSV, lazily from its parquet
file, for the caller who asked the run only (test_results_owner.py). Results are
scoped to the tenant whose folder they are in: an unknown request id, one from
another tenant, or an id that is not a plain token is ``ResultNotFound``.
"""
from __future__ import annotations

import csv
import io
import json
import types
from pathlib import Path

import pytest

from nl2sql import NL2SQL, QueryResult, ResultManifest, ResultNotFound, ResultPage, ResultSummary, UserContext
from nl2sql.api.query_api import result_from_state
from nl2sql.execution.artifacts import ArtifactStore
from nl2sql.storage.local import LocalResults
from nl2sql.execution.artifacts import results as results_module
from nl2sql.execution.dag import ExecutionDAG, LogicalEdge, LogicalNode
from nl2sql.pipeline.nodes.aggregator.node import EngineAggregatorNode
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.subgraphs.schemas import SubgraphOutput
from nl2sql.testing.runtime import fake_runtime
from nl2sql_sdk.contracts import ResultFrame

TEMPLATE = "<tenant_id>/<request_id>/<sub_query_id>.parquet"
ROWS = [{"id": i, "name": f"n{i}"} for i in range(45)]
# These runs are asked with no caller: the anonymous caller of the engine's tenant reads them (test_results_owner.py).
ASKER = UserContext()


@pytest.fixture()
def store(tmp_path):
    return ArtifactStore(LocalResults(str(tmp_path)), path_template=TEMPLATE)


def _dag():
    return ExecutionDAG(
        nodes=[LogicalNode(node_id="sq1", kind="scan", inputs=[]),
               LogicalNode(node_id="combine_g1", kind="combine", inputs=["sq1"],
                           attributes={"operation": "standalone", "join_keys": []})],
        edges=[LogicalEdge(edge_id="e1", from_id="sq1", to_id="combine_g1")],
    )


def _run(store, tenant="t1", request="run-1", rows=ROWS, truncated=False, columns=None):
    """Runs the aggregator over one scan of ``rows``; returns its state update."""
    ref = store.create_artifact_ref(ResultFrame.from_row_dicts(rows, columns=columns),
                                    {"tenant_id": tenant, "request_id": request, "sub_query_id": "sq1"})
    output = SubgraphOutput(subgraph_id="sg1", sub_query=SubQuery(id="sq1", datasource_id="d", intent="i"),
                            artifact=ref, truncated=truncated)
    node = EngineAggregatorNode(fake_runtime(settings={"tenant_id": tenant}, artifact_store=store))
    state = GraphState(trace_id=request, user_query="q", execution_dag=_dag(),
                       artifact_refs={"sq1": ref}, subgraph_outputs={"sg1": output})
    return node(state)


def _api(store, tenant="t1"):
    """``engine.results`` over a runtime whose tenant is ``tenant``."""
    engine = NL2SQL.__new__(NL2SQL)
    engine._runtime = fake_runtime(settings={"tenant_id": tenant}, artifact_store=store)
    return engine.results


# The manifest


def test_the_aggregator_writes_a_manifest_beside_the_results(store, tmp_path):
    _run(store)

    manifest = json.loads((tmp_path / "t1" / "run-1" / "manifest.json").read_text(encoding="utf-8"))

    assert manifest == {
        "request_id": "run-1",
        "owner": {"tenant_id": "t1", "user_id": None},
        "results": [{"node_id": "combine_g1", "columns": ["id", "name"], "row_count": 45, "truncated": False, "column_kinds": {"id": "number", "name": "text"}}],
    }
    assert (tmp_path / "t1" / "run-1" / "result_combine_g1.parquet").is_file()


def test_the_manifest_never_holds_rows(store, tmp_path):
    _run(store)

    text = (tmp_path / "t1" / "run-1" / "manifest.json").read_text(encoding="utf-8")

    assert "n7" not in text


def test_a_result_built_from_a_cut_sub_query_is_flagged_truncated(store):
    response = _run(store, truncated=True)["aggregator_response"]

    assert response.results == [ResultSummary(node_id="combine_g1", columns=["id", "name"],
                                              row_count=45, truncated=True,
                                              column_kinds={"id": "number", "name": "text"})]


def test_query_result_exposes_the_manifest_entries(store):
    update = _run(store)

    result = result_from_state({"trace_id": "run-1", **update})

    assert isinstance(result, QueryResult)
    assert [r.model_dump() for r in result.result_manifest] == [
        {"node_id": "combine_g1", "columns": ["id", "name"], "row_count": 45, "truncated": False, "column_kinds": {"id": "number", "name": "text"}}]


def test_the_facade_reads_the_manifest(store):
    _run(store)

    manifest = _api(store).manifest("run-1", user_context=ASKER)

    assert isinstance(manifest, ResultManifest)
    assert manifest.request_id == "run-1"
    assert [r.node_id for r in manifest.results] == ["combine_g1"]


# Paging


@pytest.mark.parametrize("offset, limit, ids, next_offset", [
    (0, 20, list(range(0, 20)), 20),     # first page
    (20, 20, list(range(20, 40)), 40),   # middle page
    (40, 20, list(range(40, 45)), None),  # last page, short
    (25, 20, list(range(25, 45)), None),  # a page that ends exactly at the last row
    (45, 20, [], None),                  # one past the end
    (1000, 20, [], None),                # far past the end
])
def test_a_page_is_the_rows_from_offset(store, offset, limit, ids, next_offset):
    _run(store)

    page = _api(store).page("run-1", "combine_g1", offset=offset, limit=limit, user_context=ASKER)

    assert isinstance(page, ResultPage)
    assert page.columns == ["id", "name"]
    assert [row[0] for row in page.rows] == ids
    assert page.rows[:1] == ([[ids[0], f"n{ids[0]}"]] if ids else [])
    assert (page.offset, page.total_rows, page.next_offset) == (offset, 45, next_offset)
    assert (page.request_id, page.result_id) == ("run-1", "combine_g1")


def test_paging_through_every_page_gives_every_row_once(store):
    _run(store)
    api, rows, offset = _api(store), [], 0

    while offset is not None:
        page = api.page("run-1", "combine_g1", offset=offset, limit=7, user_context=ASKER)
        rows.extend(page.rows)
        offset = page.next_offset

    assert rows == [[r["id"], r["name"]] for r in ROWS]


def test_the_page_size_is_capped(store):
    _run(store, rows=[{"id": i} for i in range(1500)])

    page = _api(store).page("run-1", "combine_g1", offset=0, limit=5000, user_context=ASKER)

    assert results_module.MAX_RESULT_PAGE_SIZE == 1000
    assert page.limit == 1000
    assert len(page.rows) == 1000
    assert page.next_offset == 1000


@pytest.mark.parametrize("offset, limit", [(-1, 10), (0, 0), (0, -5)])
def test_a_negative_offset_or_an_empty_page_is_refused(store, offset, limit):
    _run(store)

    with pytest.raises(ValueError):
        _api(store).page("run-1", "combine_g1", offset=offset, limit=limit, user_context=ASKER)


# CSV export


def _csv(chunks):
    return list(csv.reader(io.StringIO(b"".join(chunks).decode("utf-8"))))


def test_the_csv_holds_the_header_and_every_row(store):
    _run(store)

    lines = _csv(_api(store).csv("run-1", "combine_g1", user_context=ASKER))

    assert lines[0] == ["id", "name"]
    assert lines[1:] == [[str(r["id"]), r["name"]] for r in ROWS]


def test_the_csv_is_streamed_a_slice_at_a_time(store, monkeypatch):
    monkeypatch.setattr(results_module, "CSV_CHUNK_ROWS", 10)
    _run(store)

    stream = _api(store).csv("run-1", "combine_g1", user_context=ASKER)

    assert isinstance(stream, types.GeneratorType)
    chunks = list(stream)
    assert len(chunks) == 5  # 45 rows in slices of 10
    assert chunks[0].decode("utf-8").startswith("id,name\n")
    assert all(not c.decode("utf-8").startswith("id,name") for c in chunks[1:])
    assert len(_csv(chunks)) == 46


def test_an_empty_result_exports_its_header(store):
    _run(store, rows=[], columns=["id", "name"])

    assert b"".join(_api(store).csv("run-1", "combine_g1", user_context=ASKER)).decode("utf-8") == "id,name\n"


def test_a_missing_result_fails_before_the_csv_stream_starts(store):
    _run(store)

    with pytest.raises(ResultNotFound):
        _api(store).csv("run-1", "combine_nope", user_context=ASKER)


# Not found, other tenants, and ids that are not plain tokens


def test_an_unknown_request_id_is_not_found(store):
    _run(store)

    with pytest.raises(ResultNotFound):
        _api(store).page("run-2", "combine_g1", user_context=ASKER)
    with pytest.raises(ResultNotFound):
        _api(store).manifest("run-2", user_context=ASKER)


def test_an_unknown_result_id_is_not_found(store):
    _run(store)

    with pytest.raises(ResultNotFound):
        _api(store).page("run-1", "sq1", user_context=ASKER)


def test_another_tenants_run_is_not_found(store):
    _run(store, tenant="t2", request="theirs")

    with pytest.raises(ResultNotFound):
        _api(store, tenant="t1").page("theirs", "combine_g1", user_context=ASKER)
    with pytest.raises(ResultNotFound):
        _api(store, tenant="t1").csv("theirs", "combine_g1", user_context=ASKER)
    assert _api(store, tenant="t2").page("theirs", "combine_g1", user_context=ASKER).total_rows == 45


@pytest.mark.parametrize("request_id, result_id", [
    ("../t2/theirs", "combine_g1"),
    ("..", "combine_g1"),
    ("theirs/../theirs", "combine_g1"),
    ("theirs", "../../t2/theirs/result_combine_g1"),
    ("theirs", "combine_g1.parquet"),
    (str(Path("/abs/path")), "combine_g1"),
    ("C:\\t2\\theirs", "combine_g1"),
    ("", "combine_g1"),
    ("theirs\x00", "combine_g1"),
])
def test_an_id_that_is_not_a_plain_token_is_not_found(store, request_id, result_id):
    _run(store, tenant="t2", request="theirs")

    with pytest.raises(ResultNotFound):
        _api(store, tenant="t1").page(request_id, result_id, user_context=ASKER)


def test_a_manifest_naming_a_path_outside_the_run_is_not_trusted(store, tmp_path):
    """The manifest only lists node ids; the file read is always rendered from the template."""
    _run(store)
    _run(store, tenant="t2", request="theirs")
    path = tmp_path / "t1" / "run-1" / "manifest.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["results"][0]["node_id"] = "../../t2/theirs/result_combine_g1"
    path.write_text(json.dumps(doc), encoding="utf-8")

    with pytest.raises(ResultNotFound):
        _api(store).page("run-1", "../../t2/theirs/result_combine_g1", user_context=ASKER)
