"""Every sub-query of a run writes its own artifact.

The executor named each artifact by tenant and trace id only, so the two
sub-queries of one question rendered the same path. Run concurrently they wrote
one Parquet file at once and the aggregator read a corrupt file ("File out of
specification"); run one after the other the second overwrote the first and
the join combined a frame with itself -- a wrong answer with no error.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from nl2sql.common.settings import Settings, settings
from nl2sql.execution.artifacts import ArtifactStore, ArtifactStoreConfig
from nl2sql.execution.contracts import ExecutorRequest
from nl2sql.execution.executor.sql_executor import SqlExecutorService
from nl2sql_adapter_sdk.contracts import ResultFrame

DEFAULT_TEMPLATE = Settings.model_fields["result_artifact_path_template"].default


def _frame(value):
    return ResultFrame.from_row_dicts([{"id": 1, "value": value}])


def _metadata(sub_query_id):
    return {"tenant_id": "t1", "request_id": "r1", "schema_version": "v1", "sub_query_id": sub_query_id}


def _store(base_uri, template):
    return ArtifactStore(ArtifactStoreConfig(backend="local", base_uri=str(base_uri), path_template=template))


def test_the_default_template_names_the_sub_query():
    assert "<sub_query_id>" in DEFAULT_TEMPLATE


def test_two_sub_queries_of_one_run_get_two_artifacts_under_the_default(tmp_path):
    store = _store(tmp_path, DEFAULT_TEMPLATE)

    first = store.create_artifact_ref(_frame("a"), _metadata("sq_a"))
    second = store.create_artifact_ref(_frame("b"), _metadata("sq_b"))

    assert first.uri != second.uri
    assert Path(first.uri) == tmp_path.resolve() / "t1" / "r1" / "sq_a.parquet"
    assert store.read_result_frame(first).to_row_dicts() == [{"id": 1, "value": "a"}]
    assert store.read_result_frame(second).to_row_dicts() == [{"id": 1, "value": "b"}]


def test_a_custom_template_without_the_sub_query_is_made_unique(tmp_path):
    # A template written before <sub_query_id> existed keeps working: the
    # sub-query id is added to the file name instead of the files colliding.
    store = _store(tmp_path, "<tenant_id>/<request_id>.parquet")

    first = store.create_artifact_ref(_frame("a"), _metadata("sq_a"))
    second = store.create_artifact_ref(_frame("b"), _metadata("sq_b"))

    assert Path(first.uri) == tmp_path.resolve() / "t1" / "r1-sq_a.parquet"
    assert Path(second.uri) == tmp_path.resolve() / "t1" / "r1-sq_b.parquet"
    assert first.path_template == "<tenant_id>/<request_id>-<sub_query_id>.parquet"


def test_a_template_without_an_extension_is_made_unique_too(tmp_path):
    store = _store(tmp_path, "<tenant_id>/<request_id>")

    artifact = store.create_artifact_ref(_frame("a"), _metadata("sq_a"))

    assert Path(artifact.uri) == tmp_path.resolve() / "t1" / "r1-sq_a"


@pytest.mark.parametrize("template", ["<tenant_id>/<request_id>/<sub_query_id>.parquet",
                                      "<tenant_id>/<request_id>/<dag_node_id>.parquet"])
def test_a_template_that_already_names_the_sub_query_is_left_alone(tmp_path, template):
    store = _store(tmp_path, template)
    metadata = {**_metadata("sq_a"), "dag_node_id": "sq_a"}

    artifact = store.create_artifact_ref(_frame("a"), metadata)

    assert Path(artifact.uri) == tmp_path.resolve() / "t1" / "r1" / "sq_a.parquet"
    assert artifact.path_template == template


def test_the_artifact_records_its_sub_query(tmp_path):
    store = _store(tmp_path, DEFAULT_TEMPLATE)

    artifact = store.create_artifact_ref(_frame("a"), _metadata("sq_a"))

    assert artifact.sub_query_id == "sq_a"


class _Adapter:
    def __init__(self, value):
        self.value = value

    def execute(self, request):
        return _frame(self.value)


@pytest.mark.parametrize("template", [DEFAULT_TEMPLATE, "<tenant_id>/<request_id>.parquet"])
def test_the_executor_writes_each_sub_query_of_a_run_to_its_own_artifact(tmp_path, monkeypatch, template):
    monkeypatch.setattr(settings, "result_artifact_backend", "local")
    monkeypatch.setattr(settings, "result_artifact_base_uri", str(tmp_path))
    monkeypatch.setattr(settings, "result_artifact_path_template", template)
    adapters = {"chinook": _Adapter("music"), "support": _Adapter("tickets")}
    registry = SimpleNamespace(get_adapter=lambda ds: adapters[ds], supports=lambda ds, cap: True)
    service = SqlExecutorService(registry)

    def run(node_id, ds):
        return service.execute(ExecutorRequest(node_id=node_id, trace_id="trace-1", subgraph_name="sql_agent",
                                               datasource_id=ds, sql="SELECT 1", tenant_id="t1"))

    music, tickets = run("sq_music", "chinook"), run("sq_tickets", "support")

    assert music.artifact.uri != tickets.artifact.uri
    assert music.artifact.sub_query_id == "sq_music"
    assert service.artifact_store.read_result_frame(music.artifact).to_row_dicts()[0]["value"] == "music"
    assert service.artifact_store.read_result_frame(tickets.artifact).to_row_dicts()[0]["value"] == "tickets"
