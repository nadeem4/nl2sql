"""The price table, a run's cost, and the per-question caps.

docs/architecture/wave_a.md, section 5, row E3. A dated table in ``llm/``
prices every call, ``llm.prices`` overrides it, and a model neither lists has
a null cost (D11). ``max_llm_calls`` (60) and ``max_cost_usd`` (none) are
checked before each model call, by a ``Budget`` that cancels the run's token
with a ``RunStop`` reason, so the run ends ``stopped`` rather than retried as
a planner error (D20). A ``Budget`` also works outside a question, with caps
of its own (Wave B's profiler, V3's seeding).
"""
from __future__ import annotations

import importlib
import json
import uuid
from pathlib import Path
from typing import Any, List

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult

from nl2sql.api import query_api
from nl2sql.common.cancellation import CancellationToken
from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.config import Price
from nl2sql.limits import Budget, LimitReached, RunStop
from nl2sql.llm.prices import PRICES, PRICES_CHECKED_ON, call_cost, price_for
from nl2sql.pipeline import runtime
from nl2sql.policies import UserContext
from nl2sql.services.callbacks.token_handler import TokenUsageCallback
from nl2sql.testing.runtime import fake_runtime

_USER = UserContext(user_id="ada", roles=["analyst"])
MILLION = {"input_tokens": 1_000_000, "output_tokens": 0, "total_tokens": 1_000_000}


class _Priced(BaseChatModel):
    """A chat model that answers "ok", reports ``usage`` and its ``model_name``, and counts its calls."""

    model_name: str = "gpt-5.4"
    usage: dict = MILLION
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "priced-fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        message = AIMessage(content="ok", usage_metadata=self.usage,
                            response_metadata={"model_name": self.model_name})
        return ChatResult(generations=[ChatGeneration(message=message)])


def _call(cb: TokenUsageCallback, usage: Any, *, requested: str, reported: str, provider: str = None):
    run = uuid.uuid4()
    cb.on_chat_model_start({}, [[]], run_id=run, metadata={"langgraph_node": "ast_planner",
                                                            "ls_model_name": requested})
    meta = {"model_name": reported, **({"model_provider": provider} if provider else {})}
    message = AIMessage(content="", usage_metadata=usage, response_metadata=meta)
    cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=run)
    return cb.usage().calls[-1]


# ---------------------------------------------------------------- the price table

def test_known_model_is_priced_from_the_table():
    assert price_for("gpt-5.4", {}) == PRICES["gpt-5.4"]
    call = _call(TokenUsageCallback(), MILLION, requested="gpt-5.4", reported="gpt-5.4")
    assert call.cost == pytest.approx(2.50)


def test_config_price_overrides_the_table():
    override = {"gpt-5.4": Price(input=1.0, output=2.0)}
    price = price_for("gpt-5.4", override)
    assert (price.input, price.output, price.source) == (1.0, 2.0, "config")
    call = _call(TokenUsageCallback(prices=override), MILLION, requested="gpt-5.4", reported="gpt-5.4")
    assert call.cost == pytest.approx(1.0)
    # A model the table does not list is added by the config.
    assert price_for("my-finetune", {"my-finetune": Price(input=3, output=4)}).input == 3


def test_dated_variant_is_priced_by_its_configured_name():
    call = _call(TokenUsageCallback(), MILLION, requested="gpt-5.4", reported="gpt-5.4-2026-03-05")
    assert call.model == "gpt-5.4-2026-03-05"
    assert call.cost == pytest.approx(2.50)
    # Exact names only: gpt-5.4 never prices gpt-5.4-mini, or the other way round.
    assert price_for("gpt-5.4-mini-preview", {}) is None


def test_unknown_model_cost_is_null():
    cb = TokenUsageCallback()
    assert _call(cb, MILLION, requested="gpt-9", reported="gpt-9").cost is None
    _call(cb, MILLION, requested="gpt-5.4", reported="gpt-5.4")
    # A partial sum would understate the bill: the total is unknown too.
    assert cb.usage().total.cost is None


def test_an_anthropic_cache_write_from_the_table_costs_more_than_input():
    # #193: a 5-minute cache write is billed at 1.25x input, a cache read at 0.1x.
    usage = {"input_tokens": 1_000_000, "output_tokens": 0, "total_tokens": 1_000_000,
             "input_token_details": {"cache_read": 0, "cache_creation": 1_000_000}}
    call = _call(TokenUsageCallback(), usage, requested="claude-opus-5", reported="claude-opus-5",
                 provider="anthropic")
    assert call.cost == pytest.approx(6.25)
    assert PRICES["claude-opus-5"].cache_write > PRICES["claude-opus-5"].input


def test_a_config_price_without_a_cache_write_rate_uses_the_wires_multiplier():
    usage = {"input_tokens": 1_000_000, "output_tokens": 0, "total_tokens": 1_000_000,
             "input_token_details": {"cache_read": 0, "cache_creation": 1_000_000}}
    prices = {"claude-x": Price(input=4.0, output=20.0)}
    call = _call(TokenUsageCallback(prices=prices), usage, requested="claude-x", reported="claude-x",
                 provider="anthropic")
    assert call.cost == pytest.approx(5.0)


def test_reasoning_tokens_are_billed_as_output():
    usage = {"input_tokens": 0, "output_tokens": 1_000_000, "total_tokens": 1_000_000,
             "output_token_details": {"reasoning": 600_000}}
    call = _call(TokenUsageCallback(), usage, requested="gpt-5.4", reported="gpt-5.4")
    assert call.reasoning_tokens == 600_000
    assert call.cost == pytest.approx(15.00)


def test_unknown_counts_stay_none_never_zero():
    cb = TokenUsageCallback()
    call = _call(cb, {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
                 requested="gpt-5.4", reported="gpt-5.4")
    assert (call.cached_input_tokens, call.cache_write_input_tokens, call.reasoning_tokens) == (None, None, None)
    # Unreported details are not split out: the whole prompt is billed at the input rate.
    assert call.cost == pytest.approx((5 * 2.50 + 2 * 15.00) / 1e6)

    run = uuid.uuid4()
    cb.on_chat_model_start({}, [[]], run_id=run, metadata={"langgraph_node": "refiner", "ls_model_name": "gpt-5.4"})
    cb.on_llm_error(RuntimeError("rate limited"), run_id=run)
    failed = cb.usage().calls[-1]
    assert (failed.input_tokens, failed.output_tokens, failed.total_tokens, failed.cost) == (None, None, None, None)
    total = cb.usage().total
    assert total.calls == 2 and total.input_tokens == 5 and total.reasoning_tokens is None
    assert total.cost is None


def test_call_cost_is_none_without_the_counts_it_needs():
    assert call_cost(PRICES["gpt-5.4"], input_tokens=None, output_tokens=10) is None


def test_the_old_price_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("nl2sql.evaluation.prices")


# ---------------------------------------------------------------- the budget

def test_call_cap_stops_before_the_next_call():
    model = _Priced()
    budget = Budget(max_llm_calls=2)
    for _ in range(2):
        model.invoke("q", config={"callbacks": budget.callbacks()})
    with pytest.raises(LimitReached) as err:
        model.invoke("q", config={"callbacks": budget.callbacks()})

    assert model.calls == 2
    assert err.value.stop == RunStop(limit="max_llm_calls", value=2, spent=2)
    assert budget.stop == err.value.stop
    assert budget.token.is_cancelled() and budget.token.reason == budget.stop


def test_cost_cap_stops_before_the_next_call():
    # $2.50 a call on gpt-5.4: the second call starts under the cap, the third never does.
    model = _Priced()
    budget = Budget(max_cost_usd=4.0)
    for _ in range(2):
        model.invoke("q", config={"callbacks": budget.callbacks()})
    with pytest.raises(LimitReached):
        model.invoke("q", config={"callbacks": budget.callbacks()})

    assert model.calls == 2
    assert budget.stop == RunStop(limit="max_cost_usd", value=4.0, spent=5.0)
    assert budget.usage.usage().total.calls == 2


def test_a_budget_works_outside_a_question_and_publishes_nothing():
    from nl2sql.events import EventBus

    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    budget = Budget(max_llm_calls=1, setting="evals.max_llm_calls")
    model = FakeListChatModel(responses=["a", "b"])
    model.invoke("q", config={"callbacks": budget.callbacks()})
    with pytest.raises(LimitReached) as err:
        model.invoke("q", config={"callbacks": budget.callbacks()})
    assert seen == []
    assert "evals.max_llm_calls" in str(err.value)
    # Every later call is refused too, with the same stop.
    with pytest.raises(LimitReached):
        model.invoke("q", config={"callbacks": budget.callbacks()})
    assert budget.stop.spent == 1


def test_a_budget_cancels_every_token_bound_to_it():
    budget = Budget(max_llm_calls=1)
    token = CancellationToken()
    budget.bind(token)
    model = FakeListChatModel(responses=["a", "b"])
    model.invoke("q", config={"callbacks": budget.callbacks()})
    with pytest.raises(LimitReached):
        model.invoke("q", config={"callbacks": budget.callbacks()})
    assert token.is_cancelled() and token.reason == budget.stop
    late = CancellationToken()
    budget.bind(late)
    assert late.reason == budget.stop


def test_a_reason_is_kept_when_the_token_is_cancelled_again():
    token = CancellationToken()
    stop = RunStop(limit="max_llm_calls", value=1, spent=1)
    token.cancel(reason=stop)
    token.cancel()
    assert token.reason == stop


def test_parse_retries_count_as_calls():
    from langchain_core.prompts import ChatPromptTemplate
    from pydantic import BaseModel

    from nl2sql.llm import LLMRegistry
    from nl2sql.llm.models import AgentConfig
    from nl2sql.llm.wires import structured
    from nl2sql.secrets import SecretManager
    from nl2sql.testing.fake_llm import FakeLLMServer, Rule

    class Pick(BaseModel):
        table: str

    server = FakeLLMServer([Rule("Pick", {"table": "orders"}, when="did not match the schema"),
                            Rule("Pick", {"tabel": "orders"})]).start()
    try:
        registry = LLMRegistry(SecretManager())
        registry.register_llm(AgentConfig(provider="openai_compatible", model="m", base_url=server.base_url,
                                          structured_output="tool_calling", name="default"))
        chain = ChatPromptTemplate.from_template("Which table holds {q}?") | structured(registry.get_llm("default"),
                                                                                       Pick)
        budget = Budget(max_llm_calls=1)
        with pytest.raises(LimitReached):
            chain.invoke({"q": "orders"}, config={"callbacks": budget.callbacks()})
    finally:
        server.stop()
    assert len(server.calls) == 1
    assert budget.stop == RunStop(limit="max_llm_calls", value=1, spent=1)


# ---------------------------------------------------------------- a question that reaches its cap

class _FakeGraph:
    def __init__(self, on_invoke):
        self._on_invoke = on_invoke

    def invoke(self, state, config=None):
        return self._on_invoke(state, config)


def _planner_that_retries(model):
    """A planner as the real one is: any failure is a retryable PLANNING_FAILURE, and the router reads the token."""

    def invoke(state, config):
        token = config["configurable"]["cancellation_token"]
        errors: List[PipelineError] = []
        for _ in range(3):
            try:
                model.invoke("plan", config={"callbacks": config["callbacks"]})
            except Exception as exc:
                errors.append(PipelineError(node="ast_planner", message=str(exc), severity=ErrorSeverity.ERROR,
                                            error_code=ErrorCode.PLANNING_FAILURE))
            if token.is_cancelled():
                break
        return {"errors": errors}

    return invoke


def _stopped_run(monkeypatch, settings, model=None, **attrs):
    model = model or FakeListChatModel(responses=["a"] * 10)
    monkeypatch.setattr(runtime, "build_graph", lambda ctx, execute=True: _FakeGraph(_planner_that_retries(model)))
    ctx = fake_runtime(settings=settings, **attrs)
    return query_api.QueryAPI(ctx).run_query("How many?", user_context=_USER), model


def test_cap_hit_inside_the_planner_ends_stopped_not_planning_failure(monkeypatch):
    result, _ = _stopped_run(monkeypatch, {"pipeline": {"max_llm_calls": 1}})
    assert result.status == "stopped"
    assert [e["error_code"] for e in result.errors] == ["RUN_LIMIT_REACHED"]
    assert result.usage.total.calls == 1


def test_stopped_result_names_the_limit_and_setting(monkeypatch):
    result, _ = _stopped_run(monkeypatch, {"pipeline": {"max_llm_calls": 1}})
    assert result.stop == RunStop(limit="max_llm_calls", value=1, spent=1)
    [error] = result.errors
    assert "settings.pipeline.max_llm_calls" in error["message"]
    assert result.final_answer["summary"] == (
        "This question reached its limit of 1 model call (settings.pipeline.max_llm_calls).")


def test_a_cost_cap_stops_a_question(monkeypatch):
    result, model = _stopped_run(monkeypatch, {"pipeline": {"max_cost_usd": 1.0}}, model=_Priced())
    assert model.calls == 1
    assert result.status == "stopped"
    assert result.stop == RunStop(limit="max_cost_usd", value=1.0, spent=2.5)
    assert "$1.00" in result.final_answer["summary"] and "settings.pipeline.max_cost_usd" in result.final_answer[
        "summary"]


def test_no_cap_reached_is_a_normal_run(monkeypatch):
    model = FakeListChatModel(responses=["a"] * 10)
    monkeypatch.setattr(runtime, "build_graph",
                        lambda ctx, execute=True: _FakeGraph(lambda s, c: (model.invoke("x", config={
                            "callbacks": c["callbacks"]}), {})[1]))
    result = query_api.QueryAPI(fake_runtime()).run_query("How many?", user_context=_USER)
    assert result.status != "stopped" and result.stop is None


def test_limit_reached_is_audited(monkeypatch, tmp_path: Path):
    from nl2sql.events import AUDIT_EVENTS, STREAM_EVENTS, EventBus
    from nl2sql.observers.audit import AuditConfig, AuditObserver

    assert "run.limit_reached" in AUDIT_EVENTS and "run.limit_reached" in STREAM_EVENTS
    bus = EventBus()
    audit = AuditObserver.from_config(AuditConfig(), folder=tmp_path)
    bus.subscribe(audit)
    seen = []
    bus.subscribe(seen.append, names=["run.limit_reached"])
    _stopped_run(monkeypatch, {"pipeline": {"max_llm_calls": 1}}, bus=bus)
    audit.close()

    [event] = seen
    assert event.data == {"limit": "max_llm_calls", "value": 1.0, "spent": 1.0}
    lines = [json.loads(line) for path in (tmp_path / "audit").glob("*.jsonl")
             for line in path.read_text(encoding="utf-8").splitlines()]
    [line] = [line for line in lines if line["name"] == "run.limit_reached"]
    assert line["data"]["limit"] == "max_llm_calls"
    finished = [line for line in lines if line["name"] == "run.finished"]
    assert finished[0]["data"]["status"] == "stopped"


# ---------------------------------------------------------------- the engine

class _Client(FakeListChatModel):
    model_name: str = ""


class _Provider:
    interface_version = 2
    label = "Fake"
    api_key_env = None
    requires = ()
    install_hint = ""
    structured_output = "tool_calling"

    def create(self, agent):
        return _Client(responses=["ok"], model_name=agent.model)


def _engine(tmp_path: Path, **sections):
    from nl2sql import NL2SQL, Embeddings, Registry
    from nl2sql.testing.config import make_config

    registry = Registry.default()
    registry.add_llm("fake", _Provider())
    llm = {"provider": "fake", "model": "m1", **sections.pop("llm", {})}
    config = make_config(tmp_path, llm=llm, **sections)
    config = config.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8))})
    return NL2SQL(config, registry=registry)


def test_cost_cap_with_an_unpriced_model_is_a_config_error(tmp_path):
    from nl2sql import ConfigError

    with pytest.raises(ConfigError) as err:
        _engine(tmp_path, llm={"agents": {"refiner": {"model": "m2"}}},
                settings={"pipeline": {"max_cost_usd": 0.5}})
    text = str(err.value)
    assert "m1" in text and "m2" in text and "llm.prices" in text

    engine = _engine(tmp_path, llm={"prices": {"m1": {"input": 1, "output": 2}}},
                     settings={"pipeline": {"max_cost_usd": 0.5}})
    try:
        # A swap to a model with no price would lift the cap: it is refused too.
        with pytest.raises(ConfigError, match="m3"):
            engine.llm.replace({"provider": "fake", "model": "m3"})
    finally:
        engine.close()


def test_no_cost_cap_needs_no_prices(tmp_path):
    _engine(tmp_path).close()


def test_engine_llm_prices_is_the_effective_table(tmp_path):
    engine = _engine(tmp_path, llm={"prices": {"m1": {"input": 1, "output": 2},
                                               "gpt-5.4": {"input": 9, "output": 9}}})
    try:
        table = engine.llm.prices()
    finally:
        engine.close()
    assert table["checked_on"] == PRICES_CHECKED_ON
    rows = table["models"]
    assert rows["m1"]["source"] == "config" and rows["m1"]["input"] == 1
    assert rows["gpt-5.4"]["source"] == "config" and rows["gpt-5.4"]["input"] == 9
    assert rows["claude-opus-5"]["source"] == "table" and rows["claude-opus-5"]["cache_write"] == 6.25
    assert rows["claude-opus-5"]["citation"]


# ---------------------------------------------------------------- review fixes (PR #326)

def test_a_config_price_for_the_configured_name_wins_over_the_table_for_the_served_name():
    # A deployment alias whose responses carry a table model's name keeps its negotiated rate.
    prices = {"acme-gpt": Price(input=1.0, output=1.0)}
    call = _call(TokenUsageCallback(prices=prices), MILLION, requested="acme-gpt", reported="gpt-5.4")
    assert call.cost == pytest.approx(1.0)
    # The served name's own override still comes first.
    both = {**prices, "gpt-5.4": Price(input=3.0, output=3.0)}
    call = _call(TokenUsageCallback(prices=both), MILLION, requested="acme-gpt", reported="gpt-5.4")
    assert call.cost == pytest.approx(3.0)


@pytest.mark.parametrize("field", ["input", "output", "cached_input", "cache_write"])
def test_a_negative_price_is_rejected(field):
    from pydantic import ValidationError

    rates = {"input": 1.0, "output": 1.0, field: -1.0}
    with pytest.raises(ValidationError):
        Price(**rates)
    assert Price(input=0, output=0).input == 0


def test_a_timeout_after_a_limit_stop_reports_stopped(monkeypatch):
    import time

    model = FakeListChatModel(responses=["a"] * 10)

    def slow_planner(state, config):
        for _ in range(2):
            try:
                model.invoke("plan", config={"callbacks": config["callbacks"]})
            except Exception:
                pass
        time.sleep(1.0)  # still unwinding when the timeout fires
        return {}

    monkeypatch.setattr(runtime, "build_graph", lambda ctx, execute=True: _FakeGraph(slow_planner))
    ctx = fake_runtime(settings={"pipeline": {"max_llm_calls": 1}})
    pipeline = ctx.config.settings.pipeline.model_copy(update={"timeout_s": 0.3})
    ctx.config = ctx.config.model_copy(update={"settings": ctx.config.settings.model_copy(
        update={"pipeline": pipeline})})
    result = query_api.QueryAPI(ctx).run_query("How many?", user_context=_USER)
    assert result.status == "stopped"
    assert [e["error_code"] for e in result.errors] == ["RUN_LIMIT_REACHED"]


def test_an_eval_call_with_unknown_counts_is_counted_as_unknown_not_zero():
    from nl2sql.evaluation import run as eval_run
    from nl2sql.llm.models import AgentConfig, LLMFileConfig
    from nl2sql.services.callbacks.token_handler import LLMCallUsage, QuestionUsage

    cfg = LLMFileConfig(default=AgentConfig(provider="openai", model="gpt-5.4"), agents={})
    usage = QuestionUsage(calls=[
        LLMCallUsage(node="decomposer", input_tokens=1_000_000, output_tokens=0),
        LLMCallUsage(node="ast_planner", usage_reported=False, error="timeout"),
    ])
    assert eval_run.question_cost(usage, cfg) == pytest.approx(2.50)
    assert eval_run.unknown_cost_calls(usage) == 1
