from types import SimpleNamespace
from unittest.mock import MagicMock

from nl2sql.pipeline.nodes.answer_synthesizer.node import AnswerSynthesizerNode
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AggregatedResponse
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AnswerSynthesizerResponse
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.pipeline.state import GraphState
from nl2sql.common.errors import ErrorCode


def test_answer_synthesizer_requires_aggregated_result():
    # Validates state requirements because synthesis must not run without inputs.
    # Arrange
    ctx = SimpleNamespace(llm_registry=MagicMock())
    ctx.llm_registry.get_llm.return_value = MagicMock()
    node = AnswerSynthesizerNode(ctx)
    state = GraphState(user_query="q", aggregator_response=None)

    # Act
    result = node(state)

    # Assert
    assert result["errors"][0].error_code == ErrorCode.INVALID_STATE


def test_answer_synthesizer_returns_structured_answer():
    # Validates structured output because downstream systems rely on response shape.
    # Arrange
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = SimpleNamespace(llm_registry=MagicMock())
    ctx.llm_registry.get_llm.return_value = llm
    node = AnswerSynthesizerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.return_value = AggregatedResponse(
        summary="ok", format_type="list", content="A"
    )

    state = GraphState(
        user_query="q",
        aggregator_response=AggregatorResponse(terminal_results={"sq1": [{"id": 1}]}),
    )

    # Act
    result = node(state)

    # Assert
    assert result["answer_synthesizer_response"].final_answer["summary"] == "ok"


def test_answer_synthesizer_uses_final_answer_fallback():
    # Validates fallback because previous synthesized answer can be reused.
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = SimpleNamespace(llm_registry=MagicMock())
    ctx.llm_registry.get_llm.return_value = llm
    node = AnswerSynthesizerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.return_value = AggregatedResponse(
        summary="fallback", format_type="text", content="ok"
    )

    state = GraphState(
        user_query="q",
        aggregator_response=None,
        answer_synthesizer_response=AnswerSynthesizerResponse(
            final_answer={"summary": "prior"}
        ),
    )

    result = node(state)

    assert result["answer_synthesizer_response"].final_answer["summary"] == "fallback"


def test_answer_synthesizer_handles_llm_error():
    # Validates error handling because LLM failures must surface as errors.
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = SimpleNamespace(llm_registry=MagicMock())
    ctx.llm_registry.get_llm.return_value = llm
    node = AnswerSynthesizerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.side_effect = RuntimeError("boom")

    state = GraphState(
        user_query="q",
        aggregator_response=AggregatorResponse(terminal_results={"sq1": [{"id": 1}]}),
    )

    result = node(state)

    assert result["errors"][0].error_code == ErrorCode.AGGREGATOR_FAILED
