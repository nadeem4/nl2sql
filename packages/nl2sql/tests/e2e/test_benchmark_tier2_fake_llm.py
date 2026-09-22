"""Tier 2 end to end against the fake LLM: no key, no spending.

Each config is an ordinary LLM config YAML whose ``base_url`` points at a
``FakeLLMServer`` serving the gold plans (``GoldPlanLLM`` from tier 1). The
"bad" server has one plan deliberately wrong, so its accuracy is below 100%
and the comparison has a difference to report. Every answer carries canned
usage with cached and reasoning tokens, so the dollars can be checked
against the price table.
"""
from __future__ import annotations

import pytest
import yaml

from nl2sql import BenchmarkAPI, BenchmarkConfig
from nl2sql.common.settings import settings
from nl2sql.evaluation.prices import PRICES, call_cost
from nl2sql.evaluation.tier1 import GoldPlanLLM, load_gold_plans
from nl2sql.evaluation.tier2 import UnknownPriceError

QUESTIONS = ["chinook_001", "chinook_018", "chinook_038", "chinook_040"]
WRONG = "chinook_018"  # the bad server answers it with chinook_038's plan
USAGE = {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100,
         "prompt_tokens_details": {"cached_tokens": 400},
         "completion_tokens_details": {"reasoning_tokens": 20}}
NODE_KEY = {"datasource_resolver": "datasourceresolver", "ast_planner": "astplanner",
            "answer_synthesizer": "answersynthesizer"}


def _written_answer(llm: GoldPlanLLM):
    """A synthesizer answer written from the gold rows: the row count and the first row.

    On the right rows it is faithful. On the bad server's wrong rows for
    ``WRONG`` it names media types the rows do not hold.
    """
    def payload(_text):
        gold = llm._question.gold_result or []
        first = [f"**{v}**" if isinstance(v, str) else f"{v:,}" for v in (gold[0].values() if gold else [])]
        text = f"There are {len(gold)} rows; the first is {', '.join(first)}."
        return {"summary": text, "format_type": "text", "content": text, "warnings": []}
    return payload


def _gold_llm(wrong: bool) -> GoldPlanLLM:
    plans = load_gold_plans()
    if wrong:
        plans[WRONG] = plans["chinook_038"]
    llm = GoldPlanLLM(plans)
    for rule in llm.server.rules:
        rule.usage = USAGE
        if rule.name == "AggregatedResponse":
            rule.payload = _written_answer(llm)
    return llm


@pytest.fixture
def servers():
    good, bad = _gold_llm(False), _gold_llm(True)
    with good, bad:
        yield good, bad


def _write_config(path, base_url, helpers="gpt-5.4-mini", planner="gpt-5.4"):
    agent = {"provider": "openai", "model": helpers, "temperature": 0.0, "base_url": base_url,
             "api_key": "${env:TIER2_FAKE_KEY}"}
    cfg = {"version": 1, "default": agent,
           "agents": {"astplanner": {**agent, "model": planner}, "refiner": {**agent, "model": planner}}}
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def _run(demo_env, paths, *, servers, max_cost=5.0, passes=1, questions=QUESTIONS, spend=None):
    api = BenchmarkAPI(demo_env.ctx)
    config = BenchmarkConfig(roles=["admin"])
    configs = api.tier2_configs(config, paths)

    def select(question):
        for llm in servers:
            llm.select(question)

    def on_case(_name, _record, spent):
        if spend is not None:
            spend.append(spent)

    return api.run_tier2(config, configs, max_cost=max_cost, passes=passes, questions=questions,
                         before_case=select, on_case=on_case)


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setenv("TIER2_FAKE_KEY", "sk-fake-tier2")


def _expected_cost(record) -> float:
    model = {"astplanner": "gpt-5.4", "refiner": "gpt-5.4"}
    total = 0.0
    for node, counts in record["tokens_by_node"].items():
        price = PRICES[model.get(NODE_KEY.get(node, node), "gpt-5.4-mini")]
        total += counts["calls"] * call_cost(price, input_tokens=1000, cached_input_tokens=400,
                                             cache_write_input_tokens=0, output_tokens=100)
    return total


def test_two_configs_are_scored_priced_and_compared(demo_env, servers, tmp_path):
    good, bad = servers
    paths = {"good": _write_config(tmp_path / "good.yaml", good.server.base_url),
             "bad": _write_config(tmp_path / "bad.yaml", bad.server.base_url)}
    spend = []
    board = _run(demo_env, paths, servers=servers, spend=spend)

    assert board["stopped"] is None
    good_board, bad_board = board["configs"]["good"], board["configs"]["bad"]
    assert good_board["completed_cases"] == good_board["planned_cases"] == len(QUESTIONS)
    assert good_board["accuracy"]["overall"] == 1.0
    assert bad_board["accuracy"]["overall"] == pytest.approx(0.75)
    # The deliberately wrong plan answers a different question, so the lenient
    # score rejects it too: lenient forgives shape, never a wrong answer.
    assert good_board["accuracy"]["lenient"] == 1.0
    assert bad_board["accuracy"]["lenient"] == pytest.approx(0.75)
    assert bad_board["accuracy"]["by_tag"]["single-table"] == {
        "pass": 1, "lenient_pass": 1, "total": 2, "accuracy": 0.5, "lenient_accuracy": 0.5}
    assert all(r["lenient_status"] for r in bad_board["results"])
    assert good_board["answerability"] == {"true_refusals": 1, "false_refusals": 0, "missed_unanswerable": 0,
                                           "precision": 1.0, "recall": 1.0}
    assert good_board["models"]["astplanner"] == "openai:gpt-5.4"
    assert good_board["models"]["decomposer"] == "openai:gpt-5.4-mini"

    # Dollars: every call priced by its node's configured model, cached tokens at the cached rate.
    for record in good_board["results"]:
        assert record["cost"] == pytest.approx(_expected_cost(record))
    ast = good_board["tokens_by_node"]["ast_planner"]
    assert ast["cached_input_tokens"] == 400 * ast["calls"] and ast["reasoning_tokens"] == 20 * ast["calls"]
    assert board["spent"] == pytest.approx(good_board["cost"]["total"] + bad_board["cost"]["total"])
    assert spend == sorted(spend) and spend[-1] == pytest.approx(board["spent"])

    # Faithfulness is separate from accuracy: the unanswerable question writes no answer.
    assert good_board["faithfulness"] == {"faithful": 3, "answers": 3, "rate": 1.0, "unfaithful": []}
    assert bad_board["faithfulness"]["rate"] == pytest.approx(0.6667)
    [unfaithful] = bad_board["faithfulness"]["unfaithful"]
    assert unfaithful["id"] == WRONG and unfaithful["unsupported_entities"]

    comparison = board["comparison"]
    assert [row["config"] for row in comparison["configs"]] == ["good", "bad"]
    assert [row["faithfulness"] for row in comparison["configs"]] == [1.0, pytest.approx(0.6667)]
    assert comparison["differences"] == [{"id": WRONG, "role": "admin", "outcomes": {"good": "pass", "bad": "fail"}}]


def test_max_cost_stops_before_a_question_that_could_pass_the_cap(demo_env, servers, tmp_path):
    good, _ = servers
    paths = {"good": _write_config(tmp_path / "good.yaml", good.server.base_url, helpers="gpt-5.4")}
    board = _run(demo_env, paths, servers=servers, max_cost=0.02)

    assert board["stopped"] == "max_cost"
    completed = board["configs"]["good"]["completed_cases"]
    assert 1 <= completed < board["configs"]["good"]["planned_cases"]
    # The partial scoreboard still scores what ran.
    assert board["configs"]["good"]["accuracy"]["overall"] == 1.0
    assert board["spent"] <= 0.02 + max(r["cost"] for r in board["configs"]["good"]["results"])


def test_an_unknown_model_price_fails_before_any_call(demo_env, servers, tmp_path):
    good, _ = servers
    paths = {"good": _write_config(tmp_path / "good.yaml", good.server.base_url, planner="gpt-9-turbo")}
    with pytest.raises(UnknownPriceError, match="gpt-9-turbo"):
        _run(demo_env, paths, servers=servers)
    assert good.server.calls == []


def test_two_passes_plan_afresh_and_report_determinism(demo_env, servers, tmp_path, monkeypatch):
    # With the cache on, pass 2 would replay pass 1's plans without asking the planner.
    monkeypatch.setattr(settings, "plan_cache_enabled", True)
    good, _ = servers
    paths = {"good": _write_config(tmp_path / "good.yaml", good.server.base_url)}
    board = _run(demo_env, paths, servers=servers, passes=2)

    answerable = len(QUESTIONS) - 1
    assert sum(call["name"] == "PlanModel" for call in good.server.calls) == 2 * answerable
    assert settings.plan_cache_enabled is True  # restored after the run
    determinism = board["configs"]["good"]["determinism"]
    assert determinism == {"identical": len(QUESTIONS), "questions": len(QUESTIONS), "share": 1.0, "differing": []}
    assert board["configs"]["good"]["completed_cases"] == 2 * len(QUESTIONS)


def test_a_model_config_runs_on_every_node_and_records_the_database(demo_env, servers, monkeypatch):
    # `--model gpt-5.4` builds a config on the openai preset, so no base_url is written;
    # the OpenAI client's own OPENAI_BASE_URL variable points it at the fake server.
    from nl2sql.evaluation.presets import model_config

    good, _ = servers
    monkeypatch.setenv("OPENAI_BASE_URL", good.server.base_url)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-tier2")
    board = _run(demo_env, {"gpt-5.4": model_config("gpt-5.4")}, servers=servers)

    cfg = board["configs"]["gpt-5.4"]
    assert cfg["accuracy"]["overall"] == 1.0 and cfg["completed_cases"] == len(QUESTIONS)
    assert set(cfg["models"].values()) == {"openai:gpt-5.4"}
    assert good.server.calls
    database = board["database"]
    assert (database["datasource_id"], database["engine"]) == ("chinook", "sqlite")
    assert database["tables"] == 11 and len(database["schema_fingerprint"]) == 16
