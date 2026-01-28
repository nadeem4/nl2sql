from types import SimpleNamespace

from nl2sql.pipeline.nodes.validator.physical_node import PhysicalValidatorNode
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.common.contracts import ExecutionResult
from nl2sql.common.errors import ErrorCode


def test_physical_validator_requires_datasource_id():
    # Validates datasource requirement because validation must fail closed.
    # Arrange
    ctx = SimpleNamespace(ds_registry=SimpleNamespace())
    node = PhysicalValidatorNode(ctx)
    state = SubgraphExecutionState(trace_id="t", generator_response=GeneratorResponse(sql_draft="SELECT 1"))

    # Act
    result = node(state)

    # Assert
    assert result["errors"][0].error_code == ErrorCode.MISSING_DATASOURCE_ID


def test_physical_validator_emits_performance_warning(monkeypatch):
    # Validates performance warnings because costly queries must be flagged.
    # Arrange
    adapter = SimpleNamespace(
        datasource_id="ds1",
        datasource_engine_type="sqlite",
        connection_args={},
    )
    ctx = SimpleNamespace(ds_registry=SimpleNamespace(get_adapter=lambda _id: adapter))
    node = PhysicalValidatorNode(ctx, row_limit=10)

    responses = [
        ExecutionResult(success=True, data={"is_valid": True, "error_message": None}),
        ExecutionResult(success=True, data={"estimated_rows": 99, "estimated_bytes": 0}),
    ]

    def _fake_execute(*_a, **_k):
        return responses.pop(0)

    monkeypatch.setattr("nl2sql.common.sandbox.execute_in_sandbox", _fake_execute)
    monkeypatch.setattr(
        "nl2sql.pipeline.nodes.validator.physical_node.get_execution_pool",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr("nl2sql.common.resilience.DB_BREAKER", lambda fn: fn)

    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        generator_response=GeneratorResponse(sql_draft="SELECT * FROM users"),
    )

    # Act
    result = node(state)

    # Assert
    assert any(e.error_code == ErrorCode.PERFORMANCE_WARNING for e in result["errors"])
