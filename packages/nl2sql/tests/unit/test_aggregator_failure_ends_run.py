"""A failed aggregation ends the run with the aggregator's own error.

The answer synthesizer used to run on the empty result a failed aggregator
leaves behind, spending a model call to explain ``{}`` and adding an answer to
a run whose only true outcome is the aggregation error.
"""
from __future__ import annotations

from types import SimpleNamespace

from langgraph.graph import END

from nl2sql.common.errors import ErrorCode
from nl2sql.pipeline.nodes.aggregator import EngineAggregatorNode
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.pipeline.routes import aggregator_route


class _FailingService:
    def execute(self, dag, artifact_refs):
        raise ValueError("parquet: File out of specification")


def test_a_failed_aggregation_records_its_error_on_the_response():
    node = EngineAggregatorNode.__new__(EngineAggregatorNode)
    node.node_name = "aggregator"
    node.service = _FailingService()

    out = node(SimpleNamespace(execution_dag=object(), artifact_refs={}))

    [error] = out["errors"]
    assert error.error_code == ErrorCode.AGGREGATOR_FAILED
    assert out["aggregator_response"].errors == [error]


def test_the_run_ends_after_a_failed_aggregation():
    node = EngineAggregatorNode.__new__(EngineAggregatorNode)
    node.node_name = "aggregator"
    node.service = _FailingService()
    out = node(SimpleNamespace(execution_dag=object(), artifact_refs={}))

    assert aggregator_route(SimpleNamespace(aggregator_response=out["aggregator_response"])) == END


def test_a_successful_aggregation_goes_on_to_the_answer():
    response = AggregatorResponse(terminal_results={"combine_g1": [{"customer_id": 45}]})

    assert aggregator_route(SimpleNamespace(aggregator_response=response)) == "answer_synthesizer"
