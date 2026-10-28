"""A run a hook withheld stays unreadable by its trace id (docs/configuration/hooks.md, "What withholding guarantees").

Blanking the returned ``QueryResult`` is not enough while the run's parquet files and manifest can be read by
tenant and run id alone: the caller usually has the trace id. A withheld run leaves a marker in the results
store, and the one reader every path goes through (``page``, ``csv``, ``manifest``, ``chart``, ``head``, the
REST routes) refuses it as ``ResultNotFound``, from ``run_query`` and ``stream_query`` alike.
"""
from __future__ import annotations

import json

import polars as pl
import pytest
from langgraph.graph import END, StateGraph

from nl2sql import NL2SQL, ResultNotFound
from nl2sql.api.query_api import QueryAPI
from nl2sql.execution.artifacts import ArtifactStore
from nl2sql.execution.artifacts.results import ResultManifest, ResultReader, ResultSummary
from nl2sql.hooks import Allow, Deny, HookBinding, HookRunner
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AnswerSynthesizerResponse
from nl2sql.pipeline.state import GraphState
from nl2sql.storage.local import LocalResults
from nl2sql.testing.runtime import fake_runtime

TEMPLATE = "<tenant_id>/<request_id>/<sub_query_id>.parquet"
TENANT = "t1"


@pytest.fixture()
def store(tmp_path):
    return ArtifactStore(LocalResults(str(tmp_path)), path_template=TEMPLATE)


def _graph(store, denied):
    """A run that saves one result and its manifest, as the aggregator does, then answers (or is denied)."""

    def aggregate(state):
        trace = state.trace_id
        ref = store.write_lazy(pl.LazyFrame({"n": [1, 2, 3]}), store.run_metadata(TENANT, trace, "result_n1"))
        summary = ResultSummary(node_id="n1", columns=["n"], row_count=3, column_kinds={"n": "number"})
        store.write_manifest(TENANT, trace, ResultManifest(request_id=trace, results=[summary]))
        return {"artifact_refs": {"sq1": ref}, "aggregator_response": AggregatorResponse(results=[summary])}

    def answer(state):
        if not denied:
            return {"answer_synthesizer_response": AnswerSynthesizerResponse(final_answer={"summary": "3"})}
        runner = HookRunner([HookBinding(name="review", hook=lambda ctx: Deny(reason="Reviewed first"),
                                         points=frozenset(["answer.after"]))])
        from nl2sql.policies import UserContext

        verdict = runner.call("answer.after", trace_id=state.trace_id, question="q", user=UserContext())
        return {"answer_synthesizer_response": AnswerSynthesizerResponse(),
                "errors": [verdict.error("answersynthesizer")]}

    graph = StateGraph(GraphState)
    graph.add_node("aggregator", aggregate)
    graph.add_node("answer_synthesizer", answer)
    graph.set_entry_point("aggregator")
    graph.add_edge("aggregator", "answer_synthesizer")
    graph.add_edge("answer_synthesizer", END)
    return graph.compile()


def _run(monkeypatch, store, denied, how):
    monkeypatch.setattr("nl2sql.pipeline.runtime.build_graph", lambda ctx, execute=True: _graph(store, denied))
    runtime = fake_runtime(settings={"tenant_id": TENANT}, artifact_store=store)
    api = QueryAPI(runtime)
    if how == "run_query":
        result = api.run_query("How many?")
    else:
        from nl2sql.api.query_api import QueryResult

        result = QueryResult.model_validate(list(api.stream_query("How many?"))[-1].data["result"])
    engine = NL2SQL.__new__(NL2SQL)
    engine._runtime = runtime
    return engine, result


@pytest.mark.parametrize("how", ["run_query", "stream_query"])
def test_a_withheld_run_is_unreadable_by_its_trace_id(monkeypatch, store, how):
    engine, result = _run(monkeypatch, store, denied=True, how=how)
    trace = result.trace_id
    assert result.errors[0]["error_code"] == "HOOK_DENIED" and result.result_manifest == []

    with pytest.raises(ResultNotFound):
        engine.results.manifest(trace)
    with pytest.raises(ResultNotFound):
        engine.results.page(trace, "n1")
    with pytest.raises(ResultNotFound):
        engine.results.csv(trace, "n1")
    with pytest.raises(ResultNotFound):
        engine.results.chart(trace)
    with pytest.raises(ResultNotFound):
        engine.results.chart(trace, "n1")
    with pytest.raises(ResultNotFound):
        ResultReader(store, TENANT).head(trace, "n1", 10)


@pytest.mark.parametrize("how", ["run_query", "stream_query"])
def test_an_allowed_run_is_still_readable(monkeypatch, store, how):
    engine, result = _run(monkeypatch, store, denied=False, how=how)
    trace = result.trace_id

    assert engine.results.manifest(trace).results[0].node_id == "n1"
    assert engine.results.page(trace, "n1").rows == [[1], [2], [3]]
    assert b"1" in b"".join(engine.results.csv(trace, "n1"))
    assert ResultReader(store, TENANT).head(trace, "n1", 2)[1] == [[1], [2]]


def test_a_withheld_run_leaves_other_runs_readable(store):
    for run in ("run-a", "run-b"):
        ref = store.write_lazy(pl.LazyFrame({"n": [1]}), store.run_metadata(TENANT, run, "result_n1"))
        store.write_manifest(TENANT, run, ResultManifest(
            request_id=run, results=[ResultSummary(node_id="n1", columns=["n"], row_count=ref.row_count)]))

    store.withhold(TENANT, "run-a")

    reader = ResultReader(store, TENANT)
    with pytest.raises(ResultNotFound):
        reader.manifest("run-a")
    assert reader.manifest("run-b").results[0].node_id == "n1"


def test_the_withheld_trace_says_so_and_holds_no_rows(monkeypatch, store):
    _, result = _run(monkeypatch, store, denied=True, how="run_query")

    assert result.trace_path, "the run wrote a trace"
    with open(result.trace_path, encoding="utf-8") as stream:
        recorded = json.load(stream)["result"]
    assert recorded["errors"][0]["error_code"] == "HOOK_DENIED"
    assert recorded["result_manifest"] == [] and recorded["artifact_refs"] == {}
    assert all(sq["rows"] is None for sq in recorded["sub_queries"])
