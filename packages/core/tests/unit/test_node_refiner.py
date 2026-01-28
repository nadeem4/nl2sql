from types import SimpleNamespace

from nl2sql.pipeline.nodes.refiner.node import RefinerNode
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.common.errors import ErrorCode


def test_refiner_requires_llm():
    # Validates configuration guard because refiner must fail if LLM is missing.
    # Arrange
    ctx = SimpleNamespace(llm_registry=SimpleNamespace(get_llm=lambda _name: None))
    node = RefinerNode(ctx)
    state = SubgraphExecutionState(trace_id="t")

    # Act
    result = node(state)

    # Assert
    assert result["errors"][0].error_code == ErrorCode.MISSING_LLM
