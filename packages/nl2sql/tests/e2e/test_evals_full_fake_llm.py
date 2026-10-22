"""Mode full end to end against a fake LLM: no key, no spending.

Each config is an ``llm`` section whose ``base_url`` points at a
``FakeLLMServer`` serving the gold plans (``GoldPlanLLM``). The fakes learn
which case is being asked from the run's own ``run.started`` event. The "bad"
server has one plan deliberately wrong, so its accuracy is below 100% and the
comparison has a difference to report. Every answer carries canned usage with
cached and reasoning tokens, so the dollars can be checked against the price
table.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from nl2sql.api.evals_api import EvalsAPI
from nl2sql.config import LLM
from nl2sql.evaluation.gold_llm import GoldPlanLLM, gold_plans
from nl2sql.llm.prices import PRICES, call_cost
from nl2sql.evaluation.run import UnknownPriceError
from nl2sql.evaluation.suite import CHINOOK_SUITE_PATH, load_suite

REPO = pathlib.Path(__file__).resolve().parents[4]
CASES = {c.question: c for c in load_suite(CHINOOK_SUITE_PATH).cases}

QUESTIONS = ["chinook_001", "chinook_018", "chinook_038", "chinook_040"]
WRONG = "chinook_018"  # the bad server answers it with chinook_038's plan
USAGE = {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100,
         "prompt_tokens_details": {"cached_tokens": 400},
         "completion_tokens_details": {"reasoning_tokens": 20}}
NODE_KEY = {"datasource_resolver": "datasourceresolver", "ast_planner": "astplanner",
            "answer_synthesizer": "answersynthesizer"}


def _written_answer(llm: GoldPlanLLM):
    """A synthesizer answer written from the case's rows: the row count and the first row.

    On the right rows it is faithful. On the bad server's wrong rows for
    ``WRONG`` it names media types the rows do not hold.
    """
    rows = llm.case.rows or []
    first = [f"**{v}**" if isinstance(v, str) else f"{v:,}" for v in (rows[0].values() if rows else [])]
    text = f"There are {len(rows)} rows; the first is {', '.join(first)}."
    return {"summary": text, "format_type": "text", "content": text, "warnings": []}


def _gold_llm(wrong: bool) -> GoldPlanLLM:
    plans = gold_plans(CASES.values())
    if wrong:
        plans[WRONG] = plans["chinook_038"]
    llm = GoldPlanLLM(plans)
    for rule in llm.server.rules:
        rule.usage = USAGE
    llm.responses["AggregatedResponse"] = _written_answer
    return llm


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setenv("FULL_FAKE_KEY", "sk-fake-full")


@pytest.fixture
def servers(demo_env):
    good, bad = _gold_llm(False), _gold_llm(True)

    def select(event):
        for llm in (good, bad):
            llm.select(CASES[event.data["question"]])

    subscription = demo_env.ctx.bus.subscribe(select, names=["run.started"])
    with good, bad:
        yield good, bad
    subscription.close()


def _llm(base_url, helpers="gpt-5.4-mini", planner="gpt-5.4") -> LLM:
    return LLM(provider="openai", model=helpers, temperature=0.0, base_url=base_url, api_key="${env:FULL_FAKE_KEY}",
               agents={"ast_planner": {"model": planner}, "refiner": {"model": planner}})


def _run(ctx, llm, *, max_cost_usd=5.0, passes=1, spend=None):
    return EvalsAPI(ctx).run([CHINOOK_SUITE_PATH], mode="full", llm=llm, cases=QUESTIONS, roles=["admin"],
                             max_cost_usd=max_cost_usd, passes=passes, curated=False,
                             on_case=(lambda _n, _r, spent: spend.append(spent)) if spend is not None else None)


def _expected_cost(record) -> float:
    model = {"astplanner": "gpt-5.4", "refiner": "gpt-5.4"}
    total = 0.0
    for node, counts in record["tokens_by_node"].items():
        price = PRICES[model.get(NODE_KEY.get(node, node), "gpt-5.4-mini")]
        total += counts["calls"] * call_cost(price, input_tokens=1000, cached_input_tokens=400,
                                             cache_write_input_tokens=0, output_tokens=100)
    return total


def test_mode_full_compares_named_llm_configs(demo_env, servers):
    good, bad = servers
    spend = []
    report = _run(demo_env.ctx, {"good": _llm(good.server.base_url), "bad": _llm(bad.server.base_url)}, spend=spend)

    assert report.stopped is None
    good_board, bad_board = report.configs["good"], report.configs["bad"]
    assert report.curated is False
    assert {r["source"] for r in good_board["results"]} == {"generated"}
    assert good_board["completed_cases"] == good_board["planned_cases"] == len(QUESTIONS)
    assert good_board["accuracy"]["overall"] == 1.0 and good_board["accuracy"]["generated"] == 1.0
    assert bad_board["accuracy"]["overall"] == pytest.approx(0.75)
    # The deliberately wrong plan answers a different question, so the lenient
    # reading rejects it too: lenient forgives shape, never a wrong answer.
    assert bad_board["accuracy"]["lenient"] == pytest.approx(0.75)
    assert good_board["answerability"] == {"true_refusals": 1, "false_refusals": 0, "missed_unanswerable": 0,
                                           "precision": 1.0, "recall": 1.0}
    assert good_board["models"]["astplanner"] == "openai:gpt-5.4"
    assert good_board["models"]["decomposer"] == "openai:gpt-5.4-mini"

    # Dollars: every call priced by its node's configured model, cached tokens at the cached rate.
    for record in good_board["results"]:
        assert record["cost"] == pytest.approx(_expected_cost(record))
    ast = good_board["tokens_by_node"]["ast_planner"]
    assert ast["cached_input_tokens"] == 400 * ast["calls"] and ast["reasoning_tokens"] == 20 * ast["calls"]
    assert report.spent == pytest.approx(good_board["cost"]["total"] + bad_board["cost"]["total"])
    assert spend == sorted(spend) and spend[-1] == pytest.approx(report.spent)

    # The unanswerable case writes no answer, so faithfulness does not apply to it.
    assert good_board["faithfulness"] == {"faithful": 3, "answers": 3, "rate": 1.0, "unfaithful": []}
    [unfaithful] = bad_board["faithfulness"]["unfaithful"]
    assert unfaithful["id"] == WRONG and unfaithful["unsupported_entities"]
    # Routing: every answered case went to Chinook.
    assert good_board["scores"]["routing"] == {"applied": 3, "passed": 3, "failed": 0, "errors": 0, "mean": 1.0}

    comparison = report.comparison
    assert [row["config"] for row in comparison["configs"]] == ["good", "bad"]
    assert comparison["differences"] == [{"id": WRONG, "role": "admin", "outcomes": {"good": "pass", "bad": "fail"}}]


def test_mode_full_stops_before_a_case_that_could_pass_the_cap(demo_env, servers):
    good, _ = servers
    report = _run(demo_env.ctx, {"good": _llm(good.server.base_url, helpers="gpt-5.4")}, max_cost_usd=0.02)

    assert report.stopped == "max_cost"
    board = report.configs["good"]
    assert 1 <= board["completed_cases"] < board["planned_cases"]
    assert board["accuracy"]["overall"] == 1.0  # the partial report still scores what ran
    assert report.spent <= 0.02 + max(r["cost"] for r in board["results"])


def test_mode_full_refuses_an_unpriced_model(demo_env, servers):
    good, _ = servers
    with pytest.raises(UnknownPriceError, match="gpt-9-turbo"):
        _run(demo_env.ctx, {"good": _llm(good.server.base_url, planner="gpt-9-turbo")})
    assert good.server.calls == []


def test_mode_full_turns_the_plan_cache_off(demo_env, servers):
    # With the cache on, pass 2 would replay pass 1's plans without asking the planner.
    engine = demo_env.ctx.derive({"settings.pipeline.plan_cache": True})
    good, _ = servers
    report = _run(engine, {"good": _llm(good.server.base_url)}, passes=2)

    answerable = len(QUESTIONS) - 1
    assert sum(call["name"] == "PlannerAnswer" for call in good.server.calls) == 2 * answerable
    assert engine.settings.pipeline.plan_cache is True  # the engine's own config is untouched
    assert report.configs["good"]["determinism"]["share"] == 1.0
    assert report.configs["good"]["completed_cases"] == 2 * len(QUESTIONS)


def test_the_report_is_identified_by_the_suites_database(demo_env, servers):
    good, _ = servers
    report = _run(demo_env.ctx, {"good": _llm(good.server.base_url)})
    database = report.database
    # The demo registers three databases; the Chinook suite asks about Chinook,
    # so that alone is what the run is identified by -- with the same schema
    # fingerprint the committed `benchmarks/tier2/chinook/` runs recorded.
    assert (database["datasource_id"], database["engine"]) == ("chinook", "sqlite")
    committed = json.loads((REPO / "benchmarks" / "tier2" / "chinook"
                            / "2026-09-22_27cad30_gpt-5.4.json").read_text(encoding="utf-8"))
    assert database == committed["database"]
