from types import SimpleNamespace
from unittest.mock import MagicMock

from nl2sql.pipeline.nodes.answer_synthesizer.node import AnswerSynthesizerNode
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AggregatedResponse
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AnswerSynthesizerResponse
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.pipeline.state import GraphState
from nl2sql.common.errors import ErrorCode
from nl2sql.testing.runtime import fake_runtime


def test_answer_synthesizer_requires_aggregated_result():
    # Validates state requirements because synthesis must not run without inputs.
    # Arrange
    ctx = fake_runtime(llm_registry=MagicMock())
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
    ctx = fake_runtime(llm_registry=MagicMock())
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
    ctx = fake_runtime(llm_registry=MagicMock())
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


def _prompt_capturing_node(monkeypatch, prompts):
    from langchain_core.runnables import RunnableLambda

    def answer(prompt_value):
        prompts.append(prompt_value.to_string())
        return AggregatedResponse(summary="ok", format_type="text", content="ok")

    monkeypatch.setattr("nl2sql.pipeline.nodes.answer_synthesizer.node.structured",
                        lambda llm, schema: RunnableLambda(answer))
    ctx = fake_runtime(llm_registry=MagicMock())
    return AnswerSynthesizerNode(ctx)


def test_a_verified_answer_tells_the_model_what_the_numbers_mean(monkeypatch):
    from nl2sql.pipeline.nodes.curated_matcher.schemas import VerifiedMatch

    prompts = []
    node = _prompt_capturing_node(monkeypatch, prompts)
    match = VerifiedMatch(datasource_id="chinook", query_id="top_customers_by_revenue",
                          description="Customers ranked by invoiced revenue, before refunds", params={"n": 5})

    node(GraphState(user_query="q", verified_match=match,
                    aggregator_response=AggregatorResponse(terminal_results={"r": [{"revenue": 1}]})))

    [prompt] = prompts
    assert "Customers ranked by invoiced revenue, before refunds" in prompt
    assert "top_customers_by_revenue" in prompt


def test_a_generated_answer_prompt_is_unchanged(monkeypatch):
    from nl2sql.pipeline.nodes.answer_synthesizer.prompts import ANSWER_SYNTHESIZER_PROMPT

    prompts = []
    node = _prompt_capturing_node(monkeypatch, prompts)

    node(GraphState(user_query="q", aggregator_response=AggregatorResponse(terminal_results={"r": [{"n": 1}]})))

    [prompt] = prompts
    assert "Verified Query" not in prompt
    assert prompt.rstrip().endswith(ANSWER_SYNTHESIZER_PROMPT.rstrip().splitlines()[-1])


def test_answer_synthesizer_handles_llm_error():
    # Validates error handling because LLM failures must surface as errors.
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = fake_runtime(llm_registry=MagicMock())
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


def test_the_answer_prompt_quotes_figures_exactly_as_the_rows_hold_them():
    from nl2sql.pipeline.nodes.answer_synthesizer.prompts import ANSWER_SYNTHESIZER_PROMPT

    prompt = ANSWER_SYNTHESIZER_PROMPT.lower()

    assert "exactly as" in prompt
    assert "do not round" in prompt
    assert "thousands separators" in prompt
    assert "about" in prompt  # named as a word not to use
