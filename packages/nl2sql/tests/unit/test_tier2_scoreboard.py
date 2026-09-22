"""Tier 2's pure parts: prices, the pre-flight checks, the scoreboard and the baseline check.

The end-to-end run against a fake LLM is ``tests/e2e/test_benchmark_tier2_fake_llm.py``.
"""
import pytest

from nl2sql.configs.llm import AgentConfig, LLMFileConfig
from nl2sql.evaluation import tier2
from nl2sql.evaluation.gold import load_gold_dataset
from nl2sql.evaluation.prices import PRICES, ModelPrice, call_cost
from nl2sql.services.callbacks.token_handler import LLMCallUsage, QuestionUsage


def _cfg(default="gpt-5.4", **agents):
    return LLMFileConfig(
        default=AgentConfig(provider="openai", model=default),
        agents={k: AgentConfig(provider="openai", model=v) for k, v in agents.items()},
    )


# --- prices -------------------------------------------------------------------

def test_committed_prices_match_the_owners_verified_numbers():
    assert PRICES["gpt-5.4"][:4] == (2.50, 0.25, 2.50, 15.00)
    assert PRICES["gpt-5.4-mini"][:4] == (0.75, 0.075, 0.75, 4.50)
    opus = PRICES["claude-opus-5"]
    assert opus.cache_write == pytest.approx(opus.input * 1.25)
    assert opus.cached_input == pytest.approx(opus.input * 0.1)


def test_call_cost_bills_cached_input_at_the_cached_rate_and_reasoning_as_output():
    # 10k input of which 4k cached; 1k output of which 200 reasoning.
    cost = call_cost(PRICES["gpt-5.4"], input_tokens=10_000, cached_input_tokens=4_000,
                     cache_write_input_tokens=0, output_tokens=1_000)
    assert cost == pytest.approx((6_000 * 2.50 + 4_000 * 0.25 + 1_000 * 15.00) / 1e6)  # 0.031


def test_call_cost_bills_cache_writes_at_the_write_rate():
    price = ModelPrice(input=4.0, cached_input=0.4, cache_write=5.0, output=20.0, verified=True, source="t")
    cost = call_cost(price, input_tokens=3_000, cached_input_tokens=1_000,
                     cache_write_input_tokens=1_000, output_tokens=100)
    assert cost == pytest.approx((1_000 * 4.0 + 1_000 * 0.4 + 1_000 * 5.0 + 100 * 20.0) / 1e6)


def test_question_cost_prices_each_call_by_the_model_its_node_is_configured_with():
    cfg = _cfg("gpt-5.4-mini", astplanner="gpt-5.4")
    usage = QuestionUsage(calls=[
        LLMCallUsage(node="ast_planner", model="gpt-5.4-2026-03-05", input_tokens=1_000_000,
                     cached_input_tokens=500_000, output_tokens=0),
        LLMCallUsage(node="decomposer", model="whatever", input_tokens=0, output_tokens=1_000_000),
    ])
    # Planner on gpt-5.4: 0.5M * 2.50 + 0.5M * 0.25; decomposer on the mini default: 1M * 4.50.
    assert tier2.question_cost(usage, cfg) == pytest.approx(1.25 + 0.125 + 4.50)


# --- pre-flight -----------------------------------------------------------------

def test_a_model_missing_from_the_price_table_is_an_error_naming_config_node_and_model():
    with pytest.raises(tier2.UnknownPriceError) as err:
        tier2.check_prices({"ok": _cfg(), "bad": _cfg(refiner="gpt-9-turbo")})
    assert "bad" in str(err.value) and "refiner" in str(err.value) and "gpt-9-turbo" in str(err.value)


def test_an_unused_agent_does_not_need_a_price():
    tier2.check_prices({"x": _cfg(indexing_enrichment="gpt-9-turbo")})


def test_models_outside_verified_models_are_warned_about():
    warnings = tier2.unverified_models({"x": _cfg(decomposer="gpt-5.4-nano")})
    assert len(warnings) == 1 and "gpt-5.4-nano" in warnings[0] and "decomposer" in warnings[0]
    assert tier2.unverified_models({"x": _cfg()}) == []


def test_questions_are_selected_by_id_or_tag():
    dataset = load_gold_dataset()
    assert tier2.select_question_ids(dataset, ["chinook_001", "unanswerable"]) == [
        "chinook_001", "chinook_040", "chinook_041", "chinook_042", "chinook_043"]
    with pytest.raises(ValueError, match="nope"):
        tier2.select_question_ids(dataset, ["nope"])


def test_the_built_in_presets_are_priced_verified_and_keyless():
    from nl2sql.evaluation.presets import list_presets

    configs = dict(list_presets())
    assert set(configs) == {"gpt-5.4", "gpt-5.4-mini-helpers", "claude-planner"}
    tier2.check_prices(configs)
    assert tier2.unverified_models(configs) == []
    for cfg in configs.values():
        for agent in [cfg.default, *cfg.agents.values()]:
            assert agent.api_key.get_secret_value().startswith("${env:")
    helpers = tier2.node_agents(configs["gpt-5.4-mini-helpers"])
    assert helpers["ast_planner"].model == helpers["refiner"].model == "gpt-5.4"
    assert {helpers[n].model for n in ("datasource_resolver", "decomposer", "answer_synthesizer")} == {"gpt-5.4-mini"}
    claude = tier2.node_agents(configs["claude-planner"])
    assert claude["ast_planner"].model == "claude-opus-5" and claude["decomposer"].model == "gpt-5.4"


# --- scoreboard -----------------------------------------------------------------

def _rec(qid, status, *, expected="allowed", tags=("join",), difficulty="easy", pass_no=1, cost=0.01,
         latency=1.0, sql="SELECT 1", digest="d", refused_unanswerable=False, retries=0, errors=(),
         nodes=None, timings=None):
    return {"id": qid, "role": "admin", "pass": pass_no, "expected": expected, "status": status, "reason": "",
            "tags": list(tags), "difficulty": difficulty, "cost": cost, "latency_s": latency, "sql": sql,
            "rows_digest": digest, "refused_unanswerable": refused_unanswerable, "retries": retries,
            "error_codes": list(errors), "tokens_by_node": nodes or {}, "timings": timings or {}}


def test_scoreboard_reports_accuracy_overall_by_tag_and_by_difficulty():
    records = [
        _rec("a", "pass", tags=("join",), difficulty="easy"),
        _rec("b", "fail", tags=("join", "date"), difficulty="hard", errors=("EXECUTION_ERROR",)),
        _rec("c", "pass", tags=("date",), difficulty="hard"),
        _rec("d", "pass", expected="unanswerable", tags=("unanswerable",), refused_unanswerable=True),
    ]
    board = tier2.score_config(records, passes=1)
    assert board["accuracy"]["overall"] == pytest.approx(0.75)
    assert board["accuracy"]["by_tag"]["join"] == {"pass": 1, "total": 2, "accuracy": 0.5}
    assert board["accuracy"]["by_difficulty"]["hard"]["accuracy"] == 0.5
    assert board["errors_by_code"] == {"EXECUTION_ERROR": 1}
    assert board["determinism"] is None


def test_answerability_precision_counts_false_refusals_and_recall_counts_missed_unanswerables():
    records = [
        _rec("u1", "pass", expected="unanswerable", refused_unanswerable=True),
        _rec("u2", "fail", expected="unanswerable", refused_unanswerable=False),
        _rec("a1", "fail", refused_unanswerable=True),   # a false refusal
        _rec("a2", "pass"),
    ]
    ans = tier2.score_config(records, passes=1)["answerability"]
    assert ans == {"true_refusals": 1, "false_refusals": 1, "missed_unanswerable": 1,
                   "precision": 0.5, "recall": 0.5}


def test_scoreboard_sums_tokens_by_node_and_reports_cost_latency_and_retries():
    node = {"calls": 1, "input_tokens": 100, "cached_input_tokens": 40, "cache_write_input_tokens": 0,
            "output_tokens": 10, "reasoning_tokens": 3}
    records = [_rec("a", "pass", cost=0.02, latency=1.0, retries=1, nodes={"ast_planner": node},
                    timings={"ast_planner": 0.5}),
               _rec("b", "pass", cost=0.04, latency=3.0, nodes={"ast_planner": node},
                    timings={"ast_planner": 1.5})]
    board = tier2.score_config(records, passes=1)
    assert board["tokens_by_node"]["ast_planner"]["input_tokens"] == 200
    assert board["tokens_by_node"]["ast_planner"]["reasoning_tokens"] == 6
    assert board["cost"] == {"total": pytest.approx(0.06), "per_question": pytest.approx(0.03)}
    assert board["latency"]["question"]["p50"] == pytest.approx(2.0)
    assert board["latency"]["question"]["p95"] == pytest.approx(2.9)
    assert board["latency"]["by_node"]["ast_planner"]["p50"] == pytest.approx(1.0)
    assert board["retries"] == {"total": 1, "questions_with_retries": 1}


def test_determinism_is_the_share_of_questions_with_the_same_sql_and_rows_in_every_pass():
    records = [_rec("a", "pass", pass_no=1), _rec("a", "pass", pass_no=2),
               _rec("b", "pass", pass_no=1, digest="x"), _rec("b", "pass", pass_no=2, digest="y")]
    assert tier2.score_config(records, passes=2)["determinism"] == {
        "identical": 1, "questions": 2, "share": 0.5, "differing": ["b/admin"]}


def test_comparison_lists_each_config_and_the_questions_they_disagree_on():
    boards = {
        "good": tier2.score_config([_rec("a", "pass"), _rec("b", "pass")], passes=1),
        "bad": tier2.score_config([_rec("a", "pass"), _rec("b", "fail")], passes=1),
    }
    comparison = tier2.compare(boards)
    assert [row["config"] for row in comparison["configs"]] == ["good", "bad"]
    assert comparison["differences"] == [{"id": "b", "role": "admin", "outcomes": {"good": "pass", "bad": "fail"}}]


def _faith(faithful, unsupported=()):
    return {"faithful": faithful, "unsupported_numbers": list(unsupported), "unsupported_entities": [],
            "checked": 1}


def test_faithfulness_is_a_rate_over_answers_and_flags_each_unfaithful_one_without_touching_accuracy():
    a, b, c, d = (_rec("a", "pass"), _rec("b", "pass"), _rec("c", "fail"),
                  _rec("d", "pass", expected="unanswerable", refused_unanswerable=True))
    a["faithfulness"], b["faithfulness"], c["faithfulness"] = _faith(True), _faith(False, ["1,300"]), _faith(True)
    d["faithfulness"] = None  # a refusal writes no answer
    board = tier2.score_config([a, b, c, d], passes=1)
    assert board["faithfulness"] == {"faithful": 2, "answers": 3, "rate": pytest.approx(0.6667),
                                     "unfaithful": [{"id": "b", "role": "admin", "pass": 1,
                                                     "unsupported_numbers": ["1,300"], "unsupported_entities": []}]}
    assert board["accuracy"]["overall"] == 0.75
    assert tier2.compare({"x": board})["configs"][0]["faithfulness"] == pytest.approx(0.6667)


def test_faithfulness_is_none_when_no_answer_was_checked():
    board = tier2.score_config([_rec("a", "pass")], passes=1)
    assert board["faithfulness"] == {"faithful": 0, "answers": 0, "rate": None, "unfaithful": []}


def test_a_record_checks_the_answer_against_the_rows_it_returned():
    from nl2sql.api.query_api import QueryResult, RowSample, SubQueryResult

    question = load_gold_dataset()[0]
    result = QueryResult(
        sub_queries=[SubQueryResult(rows=RowSample(columns=["Genre", "Tracks"], rows=[["Rock", 1297]], total_rows=1))],
        final_answer={"summary": "Rock has 1,300 tracks.", "content": "Rock: 1297"})
    record = tier2._record(question, {"id": question.id, "role": "admin", "status": "pass"}, result, 1, 0.0, 0.1)
    assert record["faithfulness"]["faithful"] is False
    assert record["faithfulness"]["unsupported_numbers"] == ["1,300"]
    assert tier2._record(question, {"id": question.id, "role": "admin", "status": "fail"}, None, 1, 0.0, 0.1)[
        "faithfulness"] is None


# --- baseline -----------------------------------------------------------------------

def _board(accuracy, per_question):
    return {"configs": {"default": {"accuracy": {"overall": accuracy},
                                    "cost": {"total": 1.0, "per_question": per_question}}}}


def test_baseline_check_passes_within_the_thresholds():
    assert tier2.check_baseline(_board(0.80, 0.011), _board(0.81, 0.010),
                                max_accuracy_drop=0.02, max_cost_increase=0.2) == []


def test_baseline_check_fails_on_an_accuracy_drop_or_a_cost_rise():
    problems = tier2.check_baseline(_board(0.70, 0.02), _board(0.80, 0.01),
                                    max_accuracy_drop=0.02, max_cost_increase=0.2)
    assert len(problems) == 2
    assert "accuracy" in problems[0] and "cost" in problems[1]


def test_baseline_check_skips_configs_the_baseline_does_not_have():
    assert tier2.check_baseline({"configs": {"new": _board(0.1, 9)["configs"]["default"]}}, _board(0.9, 0.01),
                                max_accuracy_drop=0.02, max_cost_increase=0.2) == []
