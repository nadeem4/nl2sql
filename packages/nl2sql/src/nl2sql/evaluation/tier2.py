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
from nl2sql.evaluation.faithfulness import answer_text, check_answer
from nl2sql.evaluation.gold import GoldQuestion
from nl2sql.evaluation.prices import PRICES, PRICES_CHECKED_ON, ModelPrice, call_cost
from nl2sql.evaluation.types import BenchmarkConfig
from nl2sql.llm.providers import LLM_AGENTS, VERIFIED_MODELS
from nl2sql.services.callbacks.token_handler import QuestionUsage

_TOKEN_FIELDS = ("calls", "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                 "output_tokens", "reasoning_tokens")

# Defaults for the baseline check: at most two questions may flip to failing, a
# drop must be significant at p < 0.05, and cost per question may rise 20%.
DEFAULT_MAX_REGRESSIONS = 2
DEFAULT_ALPHA = 0.05
DEFAULT_MAX_COST_INCREASE = 0.20
# Deprecated: the old gate's largest allowed accuracy drop. At n = 43 two points
# is smaller than one question (2.3), so it fired on run-to-run noise. It is
# only applied when ``--max-accuracy-drop`` is passed explicitly.
DEFAULT_MAX_ACCURACY_DROP = 0.02
# 1.96 is the two-sided 95% normal quantile.
Z_95 = 1.959963984540054


class UnknownPriceError(ValueError):
    """A config names a model the price table has no row for."""


def node_agents(cfg: LLMFileConfig) -> Dict[str, AgentConfig]:
    """The agent each LLM node will run on: its own entry, else the default."""
    agents = cfg.agents or {}
    return {node: agents.get(key) or cfg.default for node, key in LLM_AGENTS.items()}


def check_prices(llm_configs: Dict[str, LLMFileConfig], prices: Dict[str, ModelPrice] = PRICES) -> None:
    """Raises before any call if a model some LLM node would call has no price."""
    missing = [f"{name}/{LLM_AGENTS[node]}: {agent.model}"
               for name, cfg in llm_configs.items()
               for node, agent in node_agents(cfg).items() if agent.model not in prices]
    if missing:
        raise UnknownPriceError(
            "No price for: " + ", ".join(missing)
            + ". Add the model to nl2sql/evaluation/prices.py (USD per 1M tokens) before running tier 2.")


def unverified_models(llm_configs: Dict[str, LLMFileConfig]) -> List[str]:
    """A warning per (config, node) whose model is not in ``VERIFIED_MODELS`` for its provider."""
    return [f"{name}/{LLM_AGENTS[node]}: {agent.provider} model '{agent.model}' is not in VERIFIED_MODELS; "
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


def _faithfulness(question: GoldQuestion, result: Optional[QueryResult]) -> Optional[Dict[str, Any]]:
    """The written answer checked against the rows it came from; None when no answer was written."""
    text = answer_text(result.final_answer) if result else ""
    if not text:
        return None
    samples = [sq.rows for sq in result.sub_queries if sq.rows is not None]
    return check_answer(text, columns=[c for s in samples for c in s.columns],
                        rows=[r for s in samples for r in s.rows], question=question.question,
                        row_counts=[s.total_rows for s in samples])


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
        "faithfulness": _faithfulness(question, result),
        # Each sub-query's intent and plan next to the SQL, so a wrong answer can
        # be traced to the plan the model wrote, and the answer the faithfulness
        # check read, so a flagged number can be seen in place.
        "plans": [{"id": sq.id, "intent": sq.intent, "plan": sq.plan}
                  for sq in (result.sub_queries if result else [])],
        "answer": answer_text(result.final_answer) if result else "",
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


def _interval(successes: int, total: int) -> Optional[List[float]]:
    bounds = wilson_interval(successes, total)
    return None if bounds is None else [round(b, 4) for b in bounds]


def wilson_interval(successes: int, total: int, z: float = Z_95) -> Optional[Tuple[float, float]]:
    """The Wilson score interval for ``successes`` of ``total``; None for no runs.

    A point estimate hides how little 43 questions can settle: 25/43 is 58.1%
    with a 95% interval of 43.3%-71.6%, so a few points either way is noise.
    Wilson rather than the normal approximation because it stays inside
    [0, 1] and behaves at 0 and 100%
    (https://www.anthropic.com/research/statistical-approach-to-model-evals).
    """
    if total <= 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def mcnemar_exact(pass_to_fail: int, fail_to_pass: int) -> float:
    """McNemar's exact two-sided p-value for the questions that flipped.

    The questions both runs got right, and both got wrong, carry no
    information about a change; only the discordant pairs do. Under "the two
    runs are equally good" each flip is a fair coin, so the p-value is the
    two-sided binomial tail. With no flips at all it is 1.0.
    """
    n = pass_to_fail + fail_to_pass
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(pass_to_fail, fail_to_pass) + 1))
    return min(1.0, 2 * tail / (2 ** n))


def _lenient(record: Dict[str, Any]) -> str:
    """A run's lenient status; a record written before lenient scoring has only the strict one."""
    return record.get("lenient_status") or record["status"]


def _pass_rate(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    passed = sum(r["status"] == "pass" for r in records)
    lenient = sum(_lenient(r) == "pass" for r in records)
    return {"pass": passed, "lenient_pass": lenient, "total": len(records),
            "accuracy": _share(passed, len(records)), "lenient_accuracy": _share(lenient, len(records))}


def _key(record: Dict[str, Any]) -> str:
    return f"{record['id']}/{record['role']}"


def _by_question(records: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    runs: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        runs.setdefault(_key(r), []).append(r)
    return runs


def _reliable(records: Sequence[Dict[str, Any]], lenient: bool = False) -> Dict[str, bool]:
    """Per question, whether **every** pass passed: the pass^k rule."""
    status = _lenient if lenient else (lambda r: r["status"])
    return {q: all(status(r) == "pass" for r in runs) for q, runs in _by_question(records).items()}


def _pass_k(records: Sequence[Dict[str, Any]], passes: int) -> Optional[Dict[str, Any]]:
    """pass^k: the share of questions that passed in every one of ``passes`` runs.

    Mean accuracy counts runs, so a question that passes twice of three times
    lifts it; pass^k counts questions and a flaky one never counts
    (tau-bench, https://arxiv.org/abs/2406.12045). Reported only when a run
    made more than one pass.
    """
    if passes <= 1:
        return None
    strict, lenient = _reliable(records), _reliable(records, lenient=True)
    return {"k": passes, "questions": len(strict),
            "strict": _share(sum(strict.values()), len(strict)),
            "lenient": _share(sum(lenient.values()), len(lenient))}


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

    # Separate from accuracy: whether the written answer's numbers and names
    # come from the rows, over every run that wrote an answer.
    answered = [r for r in records if r.get("faithfulness")]
    faithful = sum(r["faithfulness"]["faithful"] for r in answered)
    faithfulness = {
        "faithful": faithful, "answers": len(answered), "rate": _share(faithful, len(answered)),
        "unfaithful": [{"id": r["id"], "role": r["role"], "pass": r["pass"],
                        "unsupported_numbers": r["faithfulness"]["unsupported_numbers"],
                        "unsupported_entities": r["faithfulness"]["unsupported_entities"]}
                       for r in answered if not r["faithfulness"]["faithful"]],
    }

    total_cost = sum(r["cost"] for r in records)
    overall = _pass_rate(records)
    return {
        "summary": ModelEvaluator.summarize(records),
        # Strict first: today's execution match, column count and order included.
        # Lenient beside it allows extra and reordered columns and normalised
        # period labels (``ModelEvaluator.compare_results_lenient``).
        "accuracy": {
            "overall": overall["accuracy"],
            "lenient": overall["lenient_accuracy"],
            # A 95% Wilson interval on each: at n = 43 it is about +-14 points,
            # so a few points' difference between runs says nothing. It is taken
            # over the runs scored, so with several passes the runs of one
            # question are not independent and the interval reads narrower than
            # it is; pass^k below counts questions and does not.
            "interval": _interval(overall["pass"], overall["total"]),
            "lenient_interval": _interval(overall["lenient_pass"], overall["total"]),
            "pass_k": _pass_k(records, passes),
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
        "faithfulness": faithfulness,
        "results": records,
    }


def _outcome(statuses: List[str]) -> str:
    if all(s == "pass" for s in statuses):
        return "pass"
    return "fail" if not any(s == "pass" for s in statuses) else "flaky"


def compare(boards: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Configs side by side, and each question at least two configs scored differently."""
    rows = [{"config": name, "cases": len(b["results"]), "accuracy": b["accuracy"]["overall"],
             "lenient_accuracy": b["accuracy"]["lenient"],
             "answerability_precision": b["answerability"]["precision"],
             "answerability_recall": b["answerability"]["recall"],
             "cost_total": b["cost"]["total"], "cost_per_question": b["cost"]["per_question"],
             "latency_p50": b["latency"]["question"]["p50"], "latency_p95": b["latency"]["question"]["p95"],
             "retries": b["retries"]["total"],
             "determinism": (b["determinism"] or {}).get("share"),
             "faithfulness": (b.get("faithfulness") or {}).get("rate")} for name, b in boards.items()]

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


def question_flips(records: Sequence[Dict[str, Any]], baseline_records: Sequence[Dict[str, Any]]
                   ) -> Dict[str, Any]:
    """Which questions both runs ran flipped, each way, and McNemar's p-value.

    A question counts as passed when **every** pass passed, so a question that
    is flaky now and was solid before reads as a regression. Only questions
    both runs ran are compared.
    """
    now, before = _reliable(records), _reliable(baseline_records)
    shared = sorted(set(now) & set(before))
    pass_to_fail = [q for q in shared if before[q] and not now[q]]
    fail_to_pass = [q for q in shared if now[q] and not before[q]]
    return {"questions": len(shared), "pass_to_fail": pass_to_fail, "fail_to_pass": fail_to_pass,
            "p_value": round(mcnemar_exact(len(pass_to_fail), len(fail_to_pass)), 6)}


def compare_with_baseline(scoreboard: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Per config both scoreboards name, the run against the baseline question by question.

    A single run of 43 questions has a 95% interval about 14 points wide, so
    the headline difference is read alongside the questions that actually
    flipped and McNemar's exact test on them
    (https://www.anthropic.com/research/statistical-approach-to-model-evals).
    """
    out: Dict[str, Dict[str, Any]] = {}
    for name, board in scoreboard.get("configs", {}).items():
        base = baseline.get("configs", {}).get(name)
        if not base:
            continue
        out[name] = {
            **question_flips(board.get("results") or [], base.get("results") or []),
            "accuracy": board["accuracy"]["overall"], "baseline_accuracy": base["accuracy"]["overall"],
            "lenient": board["accuracy"].get("lenient"), "baseline_lenient": base["accuracy"].get("lenient"),
            "interval": board["accuracy"].get("interval"),
            "cost_per_question": board["cost"]["per_question"],
            "baseline_cost_per_question": base["cost"]["per_question"],
        }
    return out


def check_baseline(scoreboard: Dict[str, Any], baseline: Dict[str, Any], *,
                   max_regressions: int = DEFAULT_MAX_REGRESSIONS,
                   alpha: float = DEFAULT_ALPHA,
                   max_cost_increase: float = DEFAULT_MAX_COST_INCREASE,
                   max_accuracy_drop: Optional[float] = None) -> List[str]:
    """Each regression against ``baseline``, for every config both scoreboards name.

    A run fails when more than ``max_regressions`` questions flipped from
    passing to failing, or when the flips are one-sided enough for McNemar's
    exact test to put them below ``alpha``. Cost per question may still rise
    by at most ``max_cost_increase``.

    ``max_accuracy_drop`` is the deprecated flat gate and is only applied when
    it is passed: at n = 43 its old default of two points was smaller than one
    question, so it fired on run-to-run noise.
    """
    problems = []
    for name, diff in compare_with_baseline(scoreboard, baseline).items():
        lost, gained, p = len(diff["pass_to_fail"]), len(diff["fail_to_pass"]), diff["p_value"]
        if lost > max_regressions:
            problems.append(f"{name}: {lost} question{'s' if lost != 1 else ''} flipped from pass to fail "
                            f"(allowed {max_regressions}): {', '.join(diff['pass_to_fail'])}")
        elif lost > gained and p < alpha:
            problems.append(f"{name}: {lost} questions flipped to fail and {gained} the other way, "
                            f"a significant drop (McNemar p={p:.3f} < {alpha})")
        if max_accuracy_drop is not None:
            acc, base_acc = diff["accuracy"], diff["baseline_accuracy"]
            if acc is not None and base_acc is not None and base_acc - acc > max_accuracy_drop:
                problems.append(f"{name}: accuracy fell from {base_acc:.1%} to {acc:.1%} "
                                f"(allowed drop {max_accuracy_drop:.1%}, --max-accuracy-drop is deprecated)")
        cost, base_cost = diff["cost_per_question"], diff["baseline_cost_per_question"]
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
        board["models"] = {LLM_AGENTS[n]: f"{a.provider}:{a.model}" for n, a in node_agents(cfg).items()}
        board["planned_cases"], board["completed_cases"] = planned, len(records[name])
        boards[name] = board
    return {
        "tier": 2, "dataset": str(config.dataset_path), "roles": config.roles, "passes": passes,
        "questions": sorted({q.id for q, _ in cases}), "max_cost": max_cost, "spent": round(spent, 6),
        "stopped": stopped, "prices_checked_on": PRICES_CHECKED_ON, "configs": boards, "comparison": compare(boards),
    }
