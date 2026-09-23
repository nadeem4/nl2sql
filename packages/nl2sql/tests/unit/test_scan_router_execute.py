from types import SimpleNamespace

from langgraph.graph import END

from nl2sql.pipeline.routes import build_scan_layer_router


def test_router_ends_instead_of_aggregating_when_execute_is_false(monkeypatch):
    monkeypatch.setattr("nl2sql.pipeline.routes.next_scan_layer_ids", lambda dag, refs, completed=frozenset(): [])
    dag = SimpleNamespace(layers=[["sq1"]], nodes=[])
    state = SimpleNamespace(execution_dag=dag,
                            decomposer_response=None, artifact_refs={"sq1": object()}, subgraph_outputs={})
    assert build_scan_layer_router(SimpleNamespace(), execute=False)(state) == END
    sends = build_scan_layer_router(SimpleNamespace(), execute=True)(state)
    assert sends[0].node == "aggregator"


def _finished_state(artifact_refs):
    dag = SimpleNamespace(layers=[["sq1"]], nodes=[])
    return SimpleNamespace(execution_dag=dag,
                           decomposer_response=None, artifact_refs=artifact_refs,
                           subgraph_outputs={"sql_agent:sq1:t": object()})


def test_router_ends_when_no_scan_produced_an_artifact(monkeypatch):
    """A denied or failed sub-query leaves nothing to aggregate or explain.

    Dispatching the aggregator anyway raised AGGREGATOR_FAILED on the missing
    artifact and then paid for an answer-synthesizer LLM call, burying the
    real SECURITY_VIOLATION under two errors of its own.
    """
    monkeypatch.setattr("nl2sql.pipeline.routes.next_scan_layer_ids", lambda dag, refs, completed=frozenset(): [])
    assert build_scan_layer_router(SimpleNamespace(), execute=True)(_finished_state({})) == END


def test_router_still_aggregates_when_a_scan_produced_an_artifact(monkeypatch):
    monkeypatch.setattr("nl2sql.pipeline.routes.next_scan_layer_ids", lambda dag, refs, completed=frozenset(): [])
    sends = build_scan_layer_router(SimpleNamespace(), execute=True)(_finished_state({"sq1": object()}))
    assert sends[0].node == "aggregator"
