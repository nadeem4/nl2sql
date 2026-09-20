from types import SimpleNamespace

from langgraph.graph import END

from nl2sql.pipeline.routes import build_scan_layer_router


def test_router_ends_instead_of_aggregating_when_execute_is_false(monkeypatch):
    monkeypatch.setattr("nl2sql.pipeline.routes.next_scan_layer_ids", lambda dag, refs, completed=frozenset(): [])
    dag = SimpleNamespace(layers=[["sq1"]], nodes=[])
    state = SimpleNamespace(global_planner_response=SimpleNamespace(execution_dag=dag),
                            decomposer_response=None, artifact_refs={}, subgraph_outputs={})
    assert build_scan_layer_router(SimpleNamespace(), execute=False)(state) == END
    sends = build_scan_layer_router(SimpleNamespace(), execute=True)(state)
    assert sends[0].node == "aggregator"
