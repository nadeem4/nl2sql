"""A run's results are unreadable by its trace id until the hooks have ruled
(docs/configuration/hooks.md, "What withholding guarantees").

Every read (``manifest``, ``page``, ``csv``, ``chart``, ``head``, the REST routes) goes through the run's
manifest. While a hook is attached at ``answer.after`` the aggregator saves the results but not the manifest;
the node that runs ``answer.after`` writes it once the hooks allowed. So nothing is readable while the run is in
flight (the caller has the trace id from ``run.started``), and a run a hook ended, at any point, never has a
manifest. Without such a hook nothing can end the run after the aggregator, which writes the manifest itself.
These runs use the real aggregator, answer nodes and scan router; only the SQL step is a stand-in.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest
from langgraph.graph import END, StateGraph

from nl2sql import NL2SQL, ResultNotFound
from nl2sql.api.query_api import QueryAPI, QueryResult
from nl2sql.execution.artifacts import ArtifactStore
from nl2sql.execution.artifacts.results import ResultReader
from nl2sql.execution.dag import ExecutionDAG, LogicalEdge, LogicalNode
from nl2sql.hooks import Allow, Deny, HookBinding, HookRunner, NeedsApproval, hooks_of
from nl2sql.pipeline.nodes.aggregator.node import EngineAggregatorNode
from nl2sql.pipeline.nodes.answer_synthesizer.node import AnswerGateNode, AnswerSynthesizerNode
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AggregatedResponse
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.routes import aggregator_route, build_scan_layer_router
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.subgraphs.schemas import SubgraphOutput
from nl2sql.storage.local import LocalResults
from nl2sql.testing.runtime import fake_runtime

TEMPLATE = "<tenant_id>/<request_id>/<sub_query_id>.parquet"
TENANT = "t1"
RESULT = "combine_g1"
SQ = SubQuery(id="sq1", datasource_id="shop", intent="n")


@pytest.fixture()
def store(tmp_path):
    return ArtifactStore(LocalResults(str(tmp_path)), path_template=TEMPLATE)


def _dag():
    return ExecutionDAG(
        nodes=[LogicalNode(node_id="sq1", kind="scan", inputs=[]),
               LogicalNode(node_id=RESULT, kind="combine", inputs=["sq1"],
                           attributes={"operation": "standalone", "join_keys": []})],
        edges=[LogicalEdge(edge_id="e1", from_id="sq1", to_id=RESULT)],
    )


_ANSWER = AggregatedResponse(summary="Three.", format_type="text", content="There are three.", warnings=[])


def _graph(ctx, answer=True, invoke=lambda inputs: _ANSWER):
    """The pipeline from the scan router on: a stand-in SQL step, then the real router, aggregator and answer."""

    def executed(state):
        # The SQL step asks execute.before, as the executor does, and saves the sub-query's rows.
        verdict = hooks_of(ctx).call("execute.before", trace_id=state.trace_id, question=state.user_query,
                                     user=state.user_context, sub_query=SQ, sql="SELECT n FROM t")
        ref = ctx.artifact_store.write_lazy(pl.LazyFrame({"n": [1, 2, 3]}),
                                            ctx.artifact_store.run_metadata(ctx.tenant_id, state.trace_id, "sq1"))
        out = SubgraphOutput(sub_query=SQ, subgraph_id="sql_agent:sq1:t", sql_draft="SELECT n FROM t",
                             artifact=ref, status="success")
        update = {"execution_dag": _dag(), "artifact_refs": {"sq1": ref}, "subgraph_outputs": {out.subgraph_id: out}}
        return update if verdict.allowed else {**update, "errors": [verdict.error("executor")]}

    graph = StateGraph(GraphState)
    graph.add_node("executed", executed)
    graph.add_node("layer_router", lambda state: {})
    graph.add_node("aggregator", EngineAggregatorNode(ctx))
    graph.set_entry_point("executed")
    graph.add_edge("executed", "layer_router")
    graph.add_conditional_edges("layer_router", build_scan_layer_router(ctx), ["aggregator", END])
    if answer:
        node = AnswerSynthesizerNode(ctx)
        node.chain = SimpleNamespace(invoke=invoke)
        graph.add_node("answer_synthesizer", node)
        graph.add_conditional_edges("aggregator", aggregator_route, {"answer_synthesizer": "answer_synthesizer",
                                                                     END: END})
        graph.add_edge("answer_synthesizer", END)
    else:
        graph.add_node("answer_gate", AnswerGateNode(ctx))
        graph.add_edge("aggregator", "answer_gate")
        graph.add_edge("answer_gate", END)
    return graph.compile()


class Probe:
    """A hook at ``answer.after`` that tries to read the run's results by trace id, then decides."""

    name = "probe"

    def __init__(self, store, decide):
        self.store, self.decide, self.seen = store, decide, []

    def __call__(self, ctx):
        reader = ResultReader(self.store, TENANT)
        for read in (lambda: reader.manifest(ctx.trace_id), lambda: reader.page(ctx.trace_id, RESULT),
                     lambda: list(reader.csv(ctx.trace_id, RESULT))):
            try:
                read()
                self.seen.append("read")
            except ResultNotFound:
                self.seen.append("not found")
        return self.decide(ctx)


def _bind(hook, points, name=None):
    return HookBinding(name=name or getattr(hook, "name", "review"), hook=hook, points=frozenset(points))


def _runtime(monkeypatch, store, bindings, **graph):
    monkeypatch.setattr("nl2sql.pipeline.runtime.build_graph",
                        lambda ctx, execute=True, **kw: _graph(ctx, answer=kw.get("answer", True), **graph))
    return fake_runtime(settings={"tenant_id": TENANT}, artifact_store=store, hooks=HookRunner(bindings),
                        llm_registry=MagicMock(), closed=False)


def _ask(runtime, how, **kwargs):
    api = QueryAPI(runtime)
    if how == "run_query":
        return api.run_query("How many?", **kwargs)
    events = list(api.stream_query("How many?", **kwargs))
    assert events[-1].name == "run.finished", [e.name for e in events]
    return QueryResult.model_validate(events[-1].data["result"])


def _engine(runtime):
    engine = NL2SQL.__new__(NL2SQL)
    engine._runtime = runtime
    return engine


def _unreadable(engine, store, trace):
    for read in (lambda: engine.results.manifest(trace), lambda: engine.results.page(trace, RESULT),
                 lambda: engine.results.csv(trace, RESULT), lambda: engine.results.chart(trace),
                 lambda: ResultReader(store, TENANT).head(trace, RESULT, 10)):
        with pytest.raises(ResultNotFound):
            read()
    return True


def _readable(engine, trace):
    assert engine.results.manifest(trace).results[0].node_id == RESULT
    assert engine.results.page(trace, RESULT).rows == [[1], [2], [3]]
    assert b"3" in b"".join(engine.results.csv(trace, RESULT))
    return True


# ---------------------------------------------------------------- while the run is in flight


@pytest.mark.parametrize("how", ["run_query", "stream_query"])
@pytest.mark.parametrize("decision", ["deny", "allow"])
def test_nothing_is_readable_before_answer_after_has_ruled(monkeypatch, store, how, decision):
    probe = Probe(store, lambda ctx: Deny(reason="Reviewed first") if decision == "deny" else Allow())
    runtime = _runtime(monkeypatch, store, [_bind(probe, ["answer.after"])])

    result = _ask(runtime, how)

    assert probe.seen == ["not found"] * 3, "the manifest, a page and the CSV, read from inside the run"
    if decision == "deny":
        assert result.errors[0]["error_code"] == "HOOK_DENIED"
        assert _unreadable(_engine(runtime), store, result.trace_id)
    else:
        assert result.status == "success", result.errors
        assert _readable(_engine(runtime), result.trace_id)


@pytest.mark.parametrize("decision", ["deny", "allow"])
def test_the_gate_of_a_run_with_no_answer_writes_the_manifest_only_on_allow(monkeypatch, store, decision):
    probe = Probe(store, lambda ctx: Deny(reason="Reviewed first") if decision == "deny" else Allow())
    runtime = _runtime(monkeypatch, store, [_bind(probe, ["answer.after"])])

    result = QueryAPI(runtime).run_query("How many?", answer=False)

    assert probe.seen == ["not found"] * 3
    engine = _engine(runtime)
    assert _unreadable(engine, store, result.trace_id) if decision == "deny" else _readable(engine, result.trace_id)


def test_without_an_answer_after_hook_the_aggregator_writes_the_manifest(monkeypatch, store):
    trace, seen = [], []

    def answer(inputs):
        seen.append(store.read_manifest(TENANT, trace[0]) is not None)
        return _ANSWER

    runtime = _runtime(monkeypatch, store, [_bind(lambda ctx: trace.append(ctx.trace_id) or Allow(),
                                                  ["execute.before"])], invoke=answer)

    result = QueryAPI(runtime).run_query("How many?")

    assert result.status == "success", result.errors
    assert seen == [True], "written by the aggregator, before the answer is written"


# ---------------------------------------------------------------- every hook ending


def _deny(ctx):
    return Deny(reason="No")


def _raise(ctx):
    raise RuntimeError("the review service is down")


@pytest.mark.parametrize("how", ["run_query", "stream_query"])
@pytest.mark.parametrize("code, binding", [
    ("HOOK_DENIED", _bind(_deny, ["answer.after"])),
    ("HOOK_FAILED", _bind(_raise, ["answer.after"])),
    ("NEEDS_APPROVAL", _bind(lambda ctx: NeedsApproval(reason="Large scan"), ["execute.before"])),
    ("COST_LIMIT_EXCEEDED", _bind(_deny, ["execute.before"], name="policy.cost")),
])
def test_a_run_a_hook_ended_is_unreadable_by_its_trace_id(monkeypatch, store, how, code, binding):
    runtime = _runtime(monkeypatch, store, [binding])

    result = _ask(runtime, how)

    assert code in [e["error_code"] for e in result.errors]
    assert result.result_manifest == [] and result.artifact_refs == {}
    assert _unreadable(_engine(runtime), store, result.trace_id)


def test_the_approved_retry_is_a_new_run_and_readable(monkeypatch, store):
    asked = []

    def approval(ctx):
        # Asks the first time; the retry is approved (how approvals are checked is test_hook_approvals.py's).
        asked.append(ctx.trace_id)
        return NeedsApproval(reason="Large scan") if len(asked) == 1 else Allow()

    runtime = _runtime(monkeypatch, store, [_bind(approval, ["execute.before"])])
    engine = _engine(runtime)

    first = QueryAPI(runtime).run_query("How many?")
    retry = QueryAPI(runtime).run_query("How many?")

    assert first.errors[0]["error_code"] == "NEEDS_APPROVAL" and retry.status == "success", retry.errors
    assert retry.trace_id != first.trace_id
    assert _unreadable(engine, store, first.trace_id)
    assert _readable(engine, retry.trace_id)


def test_a_failed_manifest_write_fails_the_run(monkeypatch, store):
    write = store.results.write

    def failing(key, chunks):
        if key.endswith("manifest.json"):
            raise OSError("disk full")
        return write(key, chunks)

    monkeypatch.setattr(store.results, "write", failing)
    runtime = _runtime(monkeypatch, store, [_bind(lambda ctx: Allow(), ["answer.after"])])

    result = QueryAPI(runtime).run_query("How many?")

    assert result.status == "error" and "disk full" in result.errors[0]["message"]
    assert _unreadable(_engine(runtime), store, result.trace_id)


# ---------------------------------------------------------------- callers that run the graph directly


def test_an_eval_case_a_hook_ended_leaves_nothing_readable(monkeypatch, store):
    from nl2sql.evaluation.run import _ask as ask_case
    from nl2sql.evaluation.suite import EvalCase

    runtime = _runtime(monkeypatch, store, [_bind(_deny, ["answer.after"])])

    result, _ = ask_case(runtime, EvalCase.model_construct(id="c1", question="How many?"), "analyst")

    assert result.errors[0]["error_code"] == "HOOK_DENIED"
    assert _unreadable(_engine(runtime), store, result.trace_id)


def test_run_with_graph_alone_leaves_a_denied_run_unreadable(monkeypatch, store):
    from nl2sql.pipeline.runtime import run_with_graph

    runtime = _runtime(monkeypatch, store, [_bind(_deny, ["answer.after"])])

    state = run_with_graph(runtime, "How many?")

    assert _unreadable(_engine(runtime), store, state["trace_id"])
