"""A hook that stops a run withholds its data, not only its answer (docs/configuration/hooks.md).

A deny at ``answer.after`` (or any run-ending hook error) leaves the caller no
answer, no rows, no artifact references and no result manifest, from
``run_query`` and ``stream_query`` alike. While a hook is attached at
``answer.after`` the answer is not streamed token by token, since a deny
must be able to take it back.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langgraph.graph import END, StateGraph

from nl2sql.api.query_api import QueryAPI
from nl2sql.execution.artifacts.results import ResultSummary
from nl2sql.execution.contracts import ArtifactRef
from nl2sql.hooks import Allow, Deny, HookBinding, HookRunner
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AnswerSynthesizerResponse
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.subgraphs.schemas import SubgraphOutput
from nl2sql.policies import UserContext
from nl2sql.testing.runtime import fake_runtime

ARTIFACT = ArtifactRef(uri="x", format="parquet", row_count=3, columns=["n"], bytes=1, content_hash="h",
                       created_at=datetime.now(), path_template="p")
SUMMARY = ResultSummary(node_id="combine_g", columns=["n"], row_count=3)


class _Store:
    def read_result_frame(self, ref):
        return SimpleNamespace(columns=["n"], rows=[[1], [2], [3]], row_count=3)


def _bind(fn, points):
    return HookBinding(name="review", hook=fn, points=frozenset(points))


def _graph(denied, held=False):
    # The fakes release results as the real nodes do: the aggregator when no hook sits at answer.after,
    # otherwise the answer node once its hooks allowed.
    def answer(state):
        if not denied:
            return {"answer_synthesizer_response": AnswerSynthesizerResponse(final_answer={"summary": "3"}),
                    "results_released": True}
        verdict = HookRunner([_bind(lambda ctx: Deny(reason="Answers are reviewed first"), ["answer.after"])]).call(
            "answer.after", trace_id="t", question="q", user=UserContext())
        return {"answer_synthesizer_response": AnswerSynthesizerResponse(),
                "errors": [verdict.error("answersynthesizer")]}

    output = SubgraphOutput(sub_query=SubQuery(id="sq1", datasource_id="shop", intent="n"),
                            subgraph_id="sql_agent:sq1:t", sql_draft="SELECT 1", artifact=ARTIFACT, status="success")
    graph = StateGraph(GraphState)
    graph.add_node("executed", lambda state: {"subgraph_outputs": {output.subgraph_id: output},
                                              "artifact_refs": {"sq1": ARTIFACT}})
    graph.add_node("aggregator", lambda state: {"aggregator_response": AggregatorResponse(results=[SUMMARY]),
                                                "results_released": not held})
    graph.add_node("answer_synthesizer", answer)
    graph.set_entry_point("executed")
    graph.add_edge("executed", "aggregator")
    graph.add_edge("aggregator", "answer_synthesizer")
    graph.add_edge("answer_synthesizer", END)
    return graph.compile()


def _api(monkeypatch, denied, hooks=None):
    monkeypatch.setattr("nl2sql.pipeline.runtime.build_graph", lambda ctx, execute=True: _graph(denied, held=hooks is not None))
    return QueryAPI(fake_runtime(artifact_store=_Store(), **({"hooks": hooks} if hooks is not None else {})))


ANSWER_HOOK = HookRunner([_bind(lambda ctx: Allow(), ["answer.after"])])


@pytest.mark.parametrize("how", ["run_query", "stream_query"])
def test_a_deny_at_answer_after_withholds_the_rows_and_the_results(monkeypatch, how):
    api = _api(monkeypatch, denied=True)

    if how == "run_query":
        result = api.run_query("How many?").model_dump(mode="json")
    else:
        result = list(api.stream_query("How many?"))[-1].data["result"]

    assert result["final_answer"] is None
    assert result["errors"][0]["error_code"] == "HOOK_DENIED"
    assert all(sq["rows"] is None for sq in result["sub_queries"])
    assert result["artifact_refs"] == {} and result["result_manifest"] == []


def _names(events):
    return [(e.name, e.data.get("node")) for e in events]


def test_the_manifest_waits_for_answer_after_and_is_sent_once_it_allows(monkeypatch):
    events = list(_api(monkeypatch, denied=False, hooks=ANSWER_HOOK).stream_query("How many?"))

    names = _names(events)
    assert [n for n, _ in names].count("result.manifest") == 1
    assert names.index(("result.manifest", None)) > names.index(("node.finished", "answer_synthesizer"))
    assert events[-1].name == "run.finished"


def test_a_deny_at_answer_after_drops_the_streamed_manifest(monkeypatch):
    events = list(_api(monkeypatch, denied=True, hooks=ANSWER_HOOK).stream_query("How many?"))

    assert "result.manifest" not in [e.name for e in events]
    assert events[-1].name == "run.finished" and events[-1].data["result"]["result_manifest"] == []


def test_without_an_answer_after_hook_the_manifest_comes_before_the_answer(monkeypatch):
    names = _names(list(_api(monkeypatch, denied=False).stream_query("How many?")))

    assert names.index(("result.manifest", None)) < names.index(("node.started", "answer_synthesizer"))


def test_an_allowed_run_keeps_its_rows(monkeypatch):
    result = _api(monkeypatch, denied=False).run_query("How many?")

    assert result.sub_queries[0].rows.total_rows == 3
    assert result.artifact_refs and result.result_manifest


def _synthesizer(points):
    from nl2sql.pipeline.nodes.answer_synthesizer.node import AnswerSynthesizerNode

    return AnswerSynthesizerNode(fake_runtime(llm_registry=MagicMock(),
                                              hooks=HookRunner([_bind(lambda ctx: Allow(), points)])))


def test_the_answer_is_not_streamed_while_an_answer_after_hook_is_attached():
    streaming = {"configurable": {"stream_answer": True}}

    node = _synthesizer(["answer.after"])
    assert node._chain_for(streaming) is node.chain
    other = _synthesizer(["run.before"])
    assert other._chain_for(streaming) is not other.chain


def test_the_runner_says_whether_a_point_has_hooks():
    runner = HookRunner([_bind(lambda ctx: Allow(), ["answer.after"])])

    assert runner.attached("answer.after") and not runner.attached("run.before")
