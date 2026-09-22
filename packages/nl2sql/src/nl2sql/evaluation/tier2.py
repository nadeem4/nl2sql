"""Tier 2 evaluation: the real model, end to end, on the gold questions.

Tier 1 serves hand-written plans, so it tests the code nodes. Tier 2 runs the
whole product -- every LLM node on the configured models -- and scores it
with the same ``BenchmarkRunner`` and ``ModelEvaluator``. It can run several
LLM configs on the same questions, one after another, and compares them.

Every call costs money, so a run:

* prices every call from :mod:`nl2sql.evaluation.prices` by the model its
  node is configured with, and refuses to start if a model it would call has
  no price;
* takes a required dollar cap. Before each question it adds the dearest
  question seen so far to the running total; if that could pass the cap it
  stops and returns a partial scoreboard marked ``stopped: max_cost``;
* turns the plan cache off, so a second pass asks the planner again instead
  of replaying the first.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from nl2sql.api.query_api import QueryResult
from nl2sql.common.settings import settings
from nl2sql.configs.llm import AgentConfig, LLMFileConfig
from nl2sql.context import NL2SQLContext
from nl2sql.evaluation.benchmark_runner import BenchmarkRunner
from nl2sql.evaluation.evaluator import ModelEvaluator
from nl2sql.evaluation.gold import GoldQuestion
from nl2sql.evaluation.prices import PRICES, PRICES_CHECKED_ON, ModelPrice, call_cost
from nl2sql.evaluation.types import BenchmarkConfig
from nl2sql.services.callbacks.token_handler import QuestionUsage

# The pipeline's LLM nodes: the graph node name usage is recorded under, and
# the agent key the LLM config names it by.
LLM_NODES: Dict[str, str] = {
    "datasource_resolver": "datasourceresolver",
    "decomposer": "decomposer",
    "ast_planner": "astplanner",
    "refiner": "refiner",
    "answer_synthesizer": "answersynthesizer",
}
_TOKEN_FIELDS = ("calls", "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                 "output_tokens", "reasoning_tokens")

# Defaults for the baseline check: 2 accuracy points, 20% more per question.
DEFAULT_MAX_ACCURACY_DROP = 0.02
DEFAULT_MAX_COST_INCREASE = 0.20


class UnknownPriceError(ValueError):
    """A config names a model the price table has no row for."""


def node_agents(cfg: LLMFileConfig) -> Dict[str, AgentConfig]:
    """The agent each LLM node will run on: its own entry, else the default."""
    agents = cfg.agents or {}
    return {node: agents.get(key) or cfg.default for node, key in LLM_NODES.items()}


def check_prices(llm_configs: Dict[str, LLMFileConfig], prices: Dict[str, ModelPrice] = PRICES) -> None:
    """Raises before any call if a model some LLM node would call has no price."""
    missing = [f"{name}/{LLM_NODES[node]}: {agent.model}"
               for name, cfg in llm_configs.items()
               for node, agent in node_agents(cfg).items() if agent.model not in prices]
    if missing:
        raise UnknownPriceError(
            "No price for: " + ", ".join(missing)
            + ". Add the model to nl2sql/evaluation/prices.py (USD per 1M tokens) before running tier 2.")


def unverified_models(llm_configs: Dict[str, LLMFileConfig]) -> List[str]:
    """A warning per (config, node) whose model is not in ``VERIFIED_MODELS`` for its provider."""
    from nl2sql.cli.common.api_key import VERIFIED_MODELS

    return [f"{name}/{LLM_NODES[node]}: {agent.provider} model '{agent.model}' is not in VERIFIED_MODELS; "
            "the engine's parameters have not been checked against it."
            for name, cfg in llm_configs.items()
            for node, agent in node_agents(cfg).items()
            if agent.model not in VERIFIED_MODELS.get(agent.provider, {})]


def select_question_ids(dataset: Sequence[GoldQuestion], selectors: Sequence[str]) -> List[str]:
    """The ids of questions whose id or one of whose tags is in ``selectors``, in dataset order."""
    known = {q.id for q in dataset} | {t for q in dataset for t in q.tags}
    unknown = [s for s in selectors if s not in known]
    if unknown:
        raise ValueError(f"No question id or tag matches: {', '.join(unknown)}")
    wanted = set(selectors)
    return [q.id for q in dataset if q.id in wanted or wanted & set(q.tags)]


def question_cost(usage: QuestionUsage, cfg: LLMFileConfig, prices: Dict[str, ModelPrice] = PRICES) -> float:
    """Dollars for one question: each call priced by the model its node is configured with."""
    agents = node_agents(cfg)
    total = 0.0
    for call in usage.calls:
        model = (agents.get(call.node) or cfg.default).model
        total += call_cost(prices[model], input_tokens=call.input_tokens,
                           cached_input_tokens=call.cached_input_tokens,
                           cache_write_input_tokens=call.cache_write_input_tokens,
                           output_tokens=call.output_tokens)
    return total


def _digest(result: Optional[QueryResult]) -> str:
    """A fingerprint of every result set's rows, for the determinism check."""
    rows = [sq.rows.rows for sq in (result.sub_queries if result else []) if sq.rows is not None]
    return hashlib.sha1(json.dumps(rows, default=str).encode()).hexdigest()


def _record(question: GoldQuestion, row: Dict[str, Any], result: Optional[QueryResult], pass_no: int,
            cost: float, latency: float) -> Dict[str, Any]:
    """One run's row in the scoreboard: the runner's report row plus what tier 2 measures."""
    errors = result.errors if result else []
    codes = [e.get("error_code") or "UNKNOWN" for e in errors] or ([] if result else ["EXCEPTION"])
    usage = result.usage if result else QuestionUsage()
    return {
        **row, "pass": pass_no, "tags": list(question.tags), "difficulty": question.difficulty,
        "cost": round(cost, 8), "latency_s": round(latency, 4),
        "rows_digest": _digest(result), "error_codes": codes,
        "refused_unanswerable": "QUESTION_NOT_ANSWERABLE" in codes,
        "retries": sum(sq.retry_count for sq in (result.sub_queries if result else [])),
        "tokens_by_node": {node: {f: getattr(t, f) for f in _TOKEN_FIELDS} for node, t in usage.nodes.items()},
        "timings": dict(result.timings) if result else {},
    }


def _percentile(values: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolated percentile ``q`` in [0, 1]; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo), 4)


def _share(num: int, den: int) -> Optional[float]:
    return round(num / den, 4) if den else None


def _pass_rate(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    passed = sum(r["status"] == "pass" for r in records)
    return {"pass": passed, "total": len(records), "accuracy": _share(passed, len(records))}


def _key(record: Dict[str, Any]) -> str:
    return f"{record['id']}/{record['role']}"


def score_config(records: List[Dict[str, Any]], passes: int) -> Dict[str, Any]:
    """The scoreboard for one config's runs (every pass, every question)."""
    by_tag: Dict[str, List] = {}
    by_difficulty: Dict[str, List] = {}
    for r in records:
        for tag in r["tags"]:
            by_tag.setdefault(tag, []).append(r)
        by_difficulty.setdefault(r["difficulty"], []).append(r)

    unanswerable = [r for r in records if r["expected"] == "unanswerable"]
    true_refusals = sum(r["refused_unanswerable"] for r in unanswerable)
    false_refusals = sum(r["refused_unanswerable"] for r in records if r["expected"] != "unanswerable")

    tokens: Dict[str, Dict[str, int]] = {}
    for r in records:
        for node, counts in r["tokens_by_node"].items():
            totals = tokens.setdefault(node, {f: 0 for f in _TOKEN_FIELDS})
            for f in _TOKEN_FIELDS:
                totals[f] += counts.get(f, 0)

    node_times: Dict[str, List[float]] = {}
    for r in records:
        for node, seconds in r["timings"].items():
            node_times.setdefault(node, []).append(seconds)

    errors: Dict[str, int] = {}
    for r in records:
        for code in r["error_codes"]:
            errors[code] = errors.get(code, 0) + 1

    determinism = None
    if passes > 1:
        runs: Dict[str, set] = {}
        for r in records:
            runs.setdefault(_key(r), set()).add((r["sql"], r["rows_digest"]))
        differing = sorted(k for k, seen in runs.items() if len(seen) > 1)
        determinism = {"identical": len(runs) - len(differing), "questions": len(runs),
                       "share": _share(len(runs) - len(differing), len(runs)), "differing": differing}

    total_cost = sum(r["cost"] for r in records)
    return {
        "summary": ModelEvaluator.summarize(records),
        "accuracy": {
            "overall": _pass_rate(records)["accuracy"],
            "by_tag": {t: _pass_rate(rs) for t, rs in sorted(by_tag.items())},
            "by_difficulty": {d: _pass_rate(rs) for d, rs in sorted(by_difficulty.items())},
        },
        "answerability": {
            "true_refusals": true_refusals, "false_refusals": false_refusals,
            "missed_unanswerable": len(unanswerable) - true_refusals,
            "precision": _share(true_refusals, true_refusals + false_refusals),
            "recall": _share(true_refusals, len(unanswerable)),
        },
        "tokens_by_node": tokens,
        "cost": {"total": round(total_cost, 6), "per_question": round(total_cost / len(records), 6) if records else None},
        "latency": {
            "question": {"p50": _percentile([r["latency_s"] for r in records], 0.5),
                         "p95": _percentile([r["latency_s"] for r in records], 0.95)},
            "by_node": {n: {"p50": _percentile(v, 0.5), "p95": _percentile(v, 0.95)} for n, v in sorted(node_times.items())},
        },
        "retries": {"total": sum(r["retries"] for r in records),
                    "questions_with_retries": sum(r["retries"] > 0 for r in records)},
        "errors_by_code": errors,
        "determinism": determinism,
        "results": records,
    }


def _outcome(statuses: List[str]) -> str:
    if all(s == "pass" for s in statuses):
        return "pass"
    return "fail" if not any(s == "pass" for s in statuses) else "flaky"


def compare(boards: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Configs side by side, and each question at least two configs scored differently."""
    rows = [{"config": name, "cases": len(b["results"]), "accuracy": b["accuracy"]["overall"],
             "answerability_precision": b["answerability"]["precision"],
             "answerability_recall": b["answerability"]["recall"],
             "cost_total": b["cost"]["total"], "cost_per_question": b["cost"]["per_question"],
             "latency_p50": b["latency"]["question"]["p50"], "latency_p95": b["latency"]["question"]["p95"],
             "retries": b["retries"]["total"],
             "determinism": (b["determinism"] or {}).get("share")} for name, b in boards.items()]

    outcomes: Dict[tuple, Dict[str, List[str]]] = {}
    for name, b in boards.items():
        for r in b["results"]:
            outcomes.setdefault((r["id"], r["role"]), {}).setdefault(name, []).append(r["status"])
    differences = []
    for (qid, role), per_config in sorted(outcomes.items()):
        summary = {name: _outcome(statuses) for name, statuses in per_config.items()}
        if len(summary) > 1 and len(set(summary.values())) > 1:
            differences.append({"id": qid, "role": role, "outcomes": summary})
    return {"configs": rows, "differences": differences}


def check_baseline(scoreboard: Dict[str, Any], baseline: Dict[str, Any], *,
                   max_accuracy_drop: float = DEFAULT_MAX_ACCURACY_DROP,
                   max_cost_increase: float = DEFAULT_MAX_COST_INCREASE) -> List[str]:
    """Each regression against ``baseline``, for every config both scoreboards name.

    Accuracy may drop by at most ``max_accuracy_drop`` (a fraction: 0.02 is two
    points); cost per question may rise by at most ``max_cost_increase`` (a
    fraction of the baseline's: 0.2 is 20%).
    """
    problems = []
    for name, board in scoreboard.get("configs", {}).items():
        base = baseline.get("configs", {}).get(name)
        if not base:
            continue
        acc, base_acc = board["accuracy"]["overall"], base["accuracy"]["overall"]
        if acc is not None and base_acc is not None and base_acc - acc > max_accuracy_drop:
            problems.append(f"{name}: accuracy fell from {base_acc:.1%} to {acc:.1%} "
                            f"(allowed drop {max_accuracy_drop:.1%})")
        cost, base_cost = board["cost"]["per_question"], base["cost"]["per_question"]
        if cost is not None and base_cost and (cost - base_cost) / base_cost > max_cost_increase:
            problems.append(f"{name}: cost per question rose from ${base_cost:.4f} to ${cost:.4f} "
                            f"(allowed rise {max_cost_increase:.0%})")
    return problems


def run_tier2(
    ctx: NL2SQLContext,
    config: BenchmarkConfig,
    llm_configs: Dict[str, LLMFileConfig],
    *,
    max_cost: float,
    passes: int = 1,
    prices: Dict[str, ModelPrice] = PRICES,
    before_case: Optional[Callable[[GoldQuestion], None]] = None,
    on_case: Optional[Callable[[str, Dict[str, Any], float], None]] = None,
) -> Dict[str, Any]:
    """Runs every config on the selected questions, sequentially, and returns the scoreboard.

    ``max_cost`` covers the whole run, every config and pass. ``on_case`` is
    called after each question with the config name, its record and the
    dollars spent so far. ``before_case`` is passed to the runner (a test's
    fake LLM uses it). The context's LLM registry is left on the last config.
    """
    if not max_cost or max_cost <= 0:
        raise ValueError("Tier 2 needs a positive max_cost (dollars).")
    if not llm_configs:
        raise ValueError("Tier 2 needs at least one LLM config.")
    check_prices(llm_configs, prices)

    runner = BenchmarkRunner(config, ctx, workers=1, before_case=before_case)
    cases = runner.cases()
    spent, dearest, stopped = 0.0, 0.0, None
    records: Dict[str, List[Dict[str, Any]]] = {name: [] for name in llm_configs}

    # Refusals are scored against the generic message and pass 2 must plan
    # afresh, so both settings are pinned for the run and restored after.
    saved = (settings.rbac_refusal_names_tables, settings.plan_cache_enabled)
    settings.rbac_refusal_names_tables, settings.plan_cache_enabled = False, False
    try:
        for name, cfg in llm_configs.items():
            agents = dict(cfg.agents or {})
            agents["default"] = cfg.default
            ctx.llm_registry.replace_llms(agents)
            for pass_no in range(1, passes + 1):
                for question, role in cases:
                    if spent + dearest > max_cost:
                        stopped = "max_cost"
                        break
                    t0 = time.perf_counter()
                    row, result = runner.run_case(question, role)
                    latency = time.perf_counter() - t0
                    cost = question_cost(result.usage, cfg, prices) if result else 0.0
                    spent += cost
                    dearest = max(dearest, cost)
                    record = _record(question, row, result, pass_no, cost, latency)
                    records[name].append(record)
                    if on_case:
                        on_case(name, record, spent)
                if stopped:
                    break
            if stopped:
                break
    finally:
        settings.rbac_refusal_names_tables, settings.plan_cache_enabled = saved

    planned = len(cases) * passes
    boards = {}
    for name, cfg in llm_configs.items():
        board = score_config(records[name], passes)
        board["models"] = {LLM_NODES[n]: f"{a.provider}:{a.model}" for n, a in node_agents(cfg).items()}
        board["planned_cases"], board["completed_cases"] = planned, len(records[name])
        boards[name] = board
    return {
        "tier": 2, "dataset": str(config.dataset_path), "roles": config.roles, "passes": passes,
        "questions": sorted({q.id for q, _ in cases}), "max_cost": max_cost, "spent": round(spent, 6),
        "stopped": stopped, "prices_checked_on": PRICES_CHECKED_ON, "configs": boards, "comparison": compare(boards),
    }
