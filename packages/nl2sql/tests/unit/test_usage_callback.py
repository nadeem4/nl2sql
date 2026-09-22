"""Per-LLM-call token and latency telemetry, rolled up per node and per question."""

import uuid
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, Generation, LLMResult

from nl2sql.services.callbacks.token_handler import TokenUsageCallback


def _start(cb, node, run_id, model="gpt-4o"):
    cb.on_chat_model_start(
        {"name": "ChatOpenAI"},
        [[]],
        run_id=run_id,
        metadata={"langgraph_node": node, "ls_model_name": model},
    )


def _chat_result(usage=None, model="gpt-4o-2024-08-06", llm_output=None):
    msg = AIMessage(content="ok", usage_metadata=usage, response_metadata={"model_name": model})
    return LLMResult(generations=[[ChatGeneration(message=msg)]], llm_output=llm_output)


OPENAI_USAGE = {
    "input_tokens": 11000,
    "output_tokens": 300,
    "total_tokens": 11300,
    "input_token_details": {"cache_read": 7680},
    "output_token_details": {"reasoning": 128},
}


def test_records_one_call_with_node_model_tokens_and_latency():
    cb = TokenUsageCallback()
    run = uuid.uuid4()
    _start(cb, "ast_planner", run)
    cb.on_llm_end(_chat_result(OPENAI_USAGE), run_id=run)

    usage = cb.usage()
    [call] = usage.calls
    assert call.node == "ast_planner"
    assert call.model == "gpt-4o-2024-08-06"
    assert (call.input_tokens, call.cached_input_tokens, call.output_tokens, call.reasoning_tokens) == (
        11000, 7680, 300, 128)
    assert call.total_tokens == 11300
    assert call.latency_s >= 0.0
    assert call.usage_reported is True
    assert call.cost is None


def test_rolls_up_calls_per_node_and_per_question():
    cb = TokenUsageCallback()
    for node in ("decomposer", "ast_planner", "refiner", "ast_planner"):
        run = uuid.uuid4()
        _start(cb, node, run)
        cb.on_llm_end(_chat_result(OPENAI_USAGE), run_id=run)

    usage = cb.usage()
    assert usage.nodes["ast_planner"].calls == 2
    assert usage.nodes["ast_planner"].input_tokens == 22000
    assert usage.nodes["ast_planner"].cached_input_tokens == 15360
    assert usage.nodes["ast_planner"].reasoning_tokens == 256
    assert usage.nodes["decomposer"].calls == 1
    assert usage.total.calls == 4
    assert usage.total.input_tokens == 44000
    assert usage.total.output_tokens == 1200
    assert usage.total.total_tokens == 45200


def test_anthropic_shaped_usage_reads_cache_writes_too():
    cb = TokenUsageCallback()
    run = uuid.uuid4()
    _start(cb, "answer_synthesizer", run, model="claude-sonnet")
    cb.on_llm_end(_chat_result({
        "input_tokens": 500, "output_tokens": 40, "total_tokens": 540,
        "input_token_details": {"cache_read": 100, "cache_creation": 200},
    }, model="claude-sonnet"), run_id=run)

    [call] = cb.usage().calls
    assert call.cached_input_tokens == 100
    assert call.cache_write_input_tokens == 200
    assert call.reasoning_tokens == 0


def test_anthropic_cache_writes_split_by_ttl_are_counted_once():
    # langchain-anthropic zeroes the generic ``cache_creation`` when Anthropic
    # reports the write per TTL, and puts the tokens under the TTL keys.
    cb = TokenUsageCallback()
    run = uuid.uuid4()
    _start(cb, "ast_planner", run, model="claude-opus-5")
    cb.on_llm_end(_chat_result({
        "input_tokens": 3540, "output_tokens": 120, "total_tokens": 3660,
        "input_token_details": {"cache_read": 0, "cache_creation": 0,
                                "ephemeral_5m_input_tokens": 3000, "ephemeral_1h_input_tokens": 500},
    }, model="claude-opus-5"), run_id=run)

    [call] = cb.usage().calls
    assert call.cache_write_input_tokens == 3500
    assert call.input_tokens == 3540


def test_missing_details_are_zero_and_missing_usage_is_flagged():
    cb = TokenUsageCallback()
    run = uuid.uuid4()
    _start(cb, "refiner", run)
    cb.on_llm_end(_chat_result({"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}), run_id=run)
    run2 = uuid.uuid4()
    _start(cb, "refiner", run2)
    cb.on_llm_end(LLMResult(generations=[[Generation(text="x")]]), run_id=run2)

    first, second = cb.usage().calls
    assert (first.cached_input_tokens, first.reasoning_tokens, first.usage_reported) == (0, 0, True)
    assert (second.input_tokens, second.output_tokens, second.usage_reported) == (0, 0, False)
    assert cb.usage().nodes["refiner"].calls == 2


def test_falls_back_to_legacy_llm_output_token_usage():
    cb = TokenUsageCallback()
    run = uuid.uuid4()
    _start(cb, "decomposer", run)
    cb.on_llm_end(
        LLMResult(generations=[[Generation(text="x")]],
                  llm_output={"token_usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}}),
        run_id=run,
    )
    [call] = cb.usage().calls
    assert (call.input_tokens, call.output_tokens, call.total_tokens, call.usage_reported) == (9, 4, 13, True)
    assert call.model == "gpt-4o"  # from the start metadata, since the result names none


def test_a_failed_call_still_counts_with_its_latency():
    cb = TokenUsageCallback()
    run = uuid.uuid4()
    _start(cb, "ast_planner", run)
    cb.on_llm_error(RuntimeError("rate limited"), run_id=run)
    [call] = cb.usage().calls
    assert call.node == "ast_planner" and call.usage_reported is False and call.error


def test_cost_only_when_a_price_is_configured():
    prices = {"gpt-4o": {"input": 2.5, "cached_input": 1.25, "output": 10.0}}
    cb = TokenUsageCallback(prices=prices)
    run = uuid.uuid4()
    _start(cb, "ast_planner", run)
    cb.on_llm_end(_chat_result({"input_tokens": 1_000_000, "output_tokens": 100_000, "total_tokens": 1_100_000,
                                "input_token_details": {"cache_read": 400_000}}), run_id=run)

    usage = cb.usage()
    # Priced by the configured model name: the response names the dated snapshot.
    # 600k uncached * 2.5 + 400k cached * 1.25 + 100k output * 10, per million.
    assert usage.calls[0].cost == 1.5 + 0.5 + 1.0
    assert usage.nodes["ast_planner"].cost == 3.0
    assert usage.total.cost == 3.0

    run2 = uuid.uuid4()
    _start(cb, "refiner", run2, model="gpt-4o-mini")
    cb.on_llm_end(_chat_result(OPENAI_USAGE, model="gpt-4o-mini-2024-07-18"), run_id=run2)
    usage = cb.usage()
    assert usage.nodes["refiner"].cost is None
    # A partial sum would understate the bill, so the total is unknown too.
    assert usage.total.cost is None


def test_emits_the_otel_token_counter_per_token_type():
    cb = TokenUsageCallback()
    run = uuid.uuid4()
    _start(cb, "ast_planner", run)
    with patch("nl2sql.services.callbacks.token_handler.token_usage_counter") as counter:
        cb.on_llm_end(_chat_result(OPENAI_USAGE), run_id=run)
    recorded = {c.kwargs["attributes"]["type"]: c.args[0] for c in counter.add.call_args_list}
    assert recorded == {"input": 11000, "cached_input": 7680, "cache_write_input": 0,
                        "output": 300, "reasoning": 128, "total": 11300}
    attrs = counter.add.call_args_list[0].kwargs["attributes"]
    assert attrs["node"] == "ast_planner" and attrs["model"] == "gpt-4o-2024-08-06"


def test_prices_are_read_from_the_llm_prices_setting(monkeypatch):
    from nl2sql.common.settings import Settings

    monkeypatch.setenv("LLM_PRICES", '{"gpt-4o": {"input": 2.5, "cached_input": 1.25, "output": 10}}')
    assert Settings(_env_file=None).llm_prices == {"gpt-4o": {"input": 2.5, "cached_input": 1.25, "output": 10.0}}
    monkeypatch.delenv("LLM_PRICES")
    assert Settings(_env_file=None).llm_prices == {}
