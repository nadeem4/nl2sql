"""An eval run: every approved case, once per role, in one of three modes (wave_a.md, section 8, "The facade").

* ``full``: the whole pipeline on the configured LLM, or on each of several
  named LLM configs, compared. Every call costs money, so a run needs a dollar
  cap (``max_cost_usd``), refuses to start if a model it would call has no
  price, and stops before a case that could pass the cap. Each config runs on
  its own derived runtime (``ctx.derive``) with the **plan cache off**, so a
  second pass asks the planner again. Curated answers follow ``curated``
  (D12): a case a verified query answered runs again with them off, and that
  run is scored as its generated answer.
* ``plans``: each case's hand-written plan through the validator, generator
  and executor, on the gold-plan fake LLM (:mod:`nl2sql.evaluation.gold_llm`).
  No key, no cost.
* ``retrieval``: datasource routing and schema retrieval only, on the
  configured embedder. No model.

Modes ``plans`` and ``retrieval`` always run with **curated answers off**:
the curated matcher makes a model call the gold-plan fake cannot answer, and
a verified hit would skip the plan or the retrieval being measured. Every
run pins the generic refusal (``policies.refusal_names_tables`` off), which
the ``outcome`` scorer reads. The engine serving traffic is never changed.

Each case runs under its own ``RunScope`` (``QueryAPI.open_scope`` and
``close_scope``), so it publishes ``run.started`` and ``run.finished`` like any
question; every finished case publishes ``eval.case``, and the run
``eval.finished``, on the engine's bus.

A case whose outcome for a role is ``clarify`` runs only with
``clarify=True``. Wave B's clarification turns the engine's clarification on
for such a run; until then it is off, and those role runs are counted in
``skipped``.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from langchain_core.callbacks import BaseCallbackHandler

from nl2sql.api.query_api import QueryAPI, QueryResult, result_from_state
from nl2sql.config import LLM
from nl2sql.events.catalog import EvalCase as EvalCaseEvent
from nl2sql.events.catalog import EvalFinished
from nl2sql.evaluation.faithfulness import answer_text
from nl2sql.evaluation.gold_llm import GoldPlanLLM, gold_plans
from nl2sql.evaluation.prices import PRICES, PRICES_CHECKED_ON, ModelPrice, call_cost
from nl2sql.evaluation.report import EvalReport, case_status, compare, score_config
from nl2sql.evaluation.scorers import CaseRun, MODES, Scorer, build_scorers, run_scorer
from nl2sql.evaluation.scorers import retrieval_recall
from nl2sql.evaluation.suite import EvalCase, EvalSuite, select_cases
from nl2sql.llm.models import AgentConfig, LLMFileConfig
from nl2sql.llm.providers import LLM_AGENTS
from nl2sql.pipeline.runtime import run_with_graph
from nl2sql.policies import UserContext
from nl2sql.services.callbacks.token_handler import QuestionUsage

# Every row is compared, so the sample is never the thing that caps a result.
_ALL_ROWS = 100_000

LLMConfigs = Union[None, LLM, LLMFileConfig, Mapping[str, Union[LLM, LLMFileConfig]]]
OnCase = Callable[[str, Dict[str, Any], float], None]


class UnknownPriceError(ValueError):
    """A config names a model the price table has no row for."""


# ---------------------------------------------------------------- prices

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
            + ". Add the model to nl2sql/evaluation/prices.py (USD per 1M tokens) before running mode full.")


def question_cost(usage: QuestionUsage, cfg: LLMFileConfig, prices: Dict[str, ModelPrice] = PRICES) -> float:
    """Dollars for one case: each call priced by the model its node is configured with."""
    agents = node_agents(cfg)
    total = 0.0
    for call in usage.calls:
        model = (agents.get(call.node) or cfg.default).model
        total += call_cost(prices[model], input_tokens=call.input_tokens,
                           cached_input_tokens=call.cached_input_tokens,
                           cache_write_input_tokens=call.cache_write_input_tokens,
                           output_tokens=call.output_tokens)
    return total


def llm_configs(ctx: Any, llm: LLMConfigs) -> Dict[str, LLMFileConfig]:
    """The named configs a mode full run compares: the engine's own ``llm`` section as ``default`` when None."""
    if llm is None:
        return {"default": LLMFileConfig.from_llm(ctx.config.llm)}
    if isinstance(llm, (LLM, LLMFileConfig)):
        llm = {"default": llm}
    if not llm:
        raise ValueError("Mode full needs at least one LLM config.")
    return {name: cfg if isinstance(cfg, LLMFileConfig) else LLMFileConfig.from_llm(cfg)
            for name, cfg in llm.items()}


# ---------------------------------------------------------------- one case

class _Sent(BaseCallbackHandler):
    """What the run's schema retriever sent the planner, read from the node's own output."""

    def __init__(self) -> None:
        self._runs: set = set()
        self.tables: Optional[Dict[str, List[str]]] = None

    def on_chain_start(self, serialized, inputs, *, run_id, metadata=None, **kwargs: Any) -> None:
        name = kwargs.get("name") or (serialized or {}).get("name")
        if (metadata or {}).get("langgraph_node") == "schema_retriever" and name == "schema_retriever":
            self._runs.add(run_id)

    def on_chain_end(self, outputs, *, run_id, **kwargs: Any) -> None:
        if run_id not in self._runs or not isinstance(outputs, dict):
            return
        tables = self.tables if self.tables is not None else {}
        for table in (outputs.get("retrieval") or {}).get("tables", []):
            columns = tables.setdefault(table["table"], [])
            columns.extend(c for c in table["columns"] if c not in columns)
        self.tables = tables


def _ask(run: Any, case: EvalCase, role: str) -> Tuple[QueryResult, Optional[Dict[str, List[str]]]]:
    """One case, asked as ``role``, in a run scope of its own: the result, with every row, and what was retrieved."""
    api = QueryAPI(run)
    user = UserContext(roles=[role])
    scope = api.open_scope(case.question, user_context=user)
    sent = _Sent()
    try:
        state = run_with_graph(run, case.question, user_context=user, callbacks=[sent], scope=scope)
        result = result_from_state(state, artifact_store=getattr(run, "artifact_store", None),
                                   sample_rows=_ALL_ROWS)
    except Exception as exc:
        scope.fail(f"The run failed: {exc}")
        raise
    api.close_scope(scope, result)
    return result, sent.tables


def _measure_retrieval(run: Any, case: EvalCase, role: str) -> Tuple[Dict[str, List[str]], List[str]]:
    """Mode retrieval: what the schema retriever sends for ``case`` as ``role``, and the datasources routing ranks."""
    limit = run.settings.routing.max_datasources
    return retrieval_recall.sent(run, case, role), retrieval_recall.routed(run, case.question, limit)


def _digest(result: QueryResult) -> str:
    """A fingerprint of every result set's rows, for the determinism check."""
    rows = [sq.rows.rows for sq in result.sub_queries if sq.rows is not None]
    return hashlib.sha1(json.dumps(rows, default=str).encode()).hexdigest()


def _scored(case: EvalCase, role: str, mode: str, scorers: Dict[str, Scorer], result: QueryResult,
            retrieval: Optional[Dict[str, List[str]]] = None, routed: Optional[List[str]] = None
            ) -> Dict[str, Any]:
    """Each scorer's score on one run, and the case's status from them."""
    samples = [sq.rows for sq in result.sub_queries if sq.rows is not None]
    case_run = CaseRun(case=case, role=role, mode=mode, result=result,
                       rows=samples[0].rows if samples else None,
                       columns=samples[0].columns if samples else [], retrieval=retrieval, routed=routed)
    scores = {name: run_scorer(scorer, case_run).model_dump(mode="json") for name, scorer in scorers.items()}
    status, reason = case_status(scores)
    lenient_status, _ = case_status(scores, lenient=True)
    return {"status": status, "reason": reason, "lenient_status": lenient_status, "scores": scores}


def _record(suite: str, case: EvalCase, role: str, pass_no: int, scored: Dict[str, Any],
            result: Optional[QueryResult], cost: float, latency: float) -> Dict[str, Any]:
    """One case run's record: the case, its scores, and what the run measured."""
    result = result or QueryResult()
    usage = result.usage
    codes = [e.get("error_code") or "UNKNOWN" for e in result.errors]
    samples = [sq.rows for sq in result.sub_queries if sq.rows is not None]
    faithfulness = scored["scores"].get("faithfulness", {})
    return {
        "suite": suite, "id": case.id, "question": case.question, "role": role,
        "expected": case.outcome[role], **scored, "pass": pass_no,
        "tags": list(case.tags), "difficulty": case.difficulty,
        "sql": result.sub_queries[0].sql if result.sub_queries else "",
        "rows": samples[0].total_rows if samples else None, "gold_rows": len(case.rows or []),
        "cost": round(cost, 8), "latency_s": round(latency, 4), "rows_digest": _digest(result),
        "error_codes": codes, "refused_unanswerable": "QUESTION_NOT_ANSWERABLE" in codes,
        "retries": sum(sq.retry_count for sq in result.sub_queries),
        "tokens_by_node": {node: {f: getattr(t, f) for f in ("calls", "input_tokens", "cached_input_tokens",
                                                             "cache_write_input_tokens", "output_tokens",
                                                             "reasoning_tokens")}
                           for node, t in usage.nodes.items()},
        "timings": dict(result.timings),
        "faithfulness": faithfulness.get("detail") if faithfulness.get("passed") is not None else None,
        # Each sub-query's intent and plan next to the SQL, so a wrong answer can
        # be traced to the plan the model wrote.
        "plans": [{"id": sq.id, "intent": sq.intent, "plan": sq.plan} for sq in result.sub_queries],
        "answer": answer_text(result.final_answer),
        # A verified query's answer has no plan the model wrote.
        "source": result.source.type, "verified_query": result.source.id,
        "generated": None,
    }


# ---------------------------------------------------------------- the run

class _Case:
    """One (suite, case, role) a run will ask."""

    def __init__(self, suite: str, case: EvalCase, role: str):
        self.suite, self.case, self.role = suite, case, role


def _jobs(suites: Sequence[EvalSuite], cases: Optional[Sequence[str]], roles: Optional[Sequence[str]],
          clarify: bool) -> Tuple[List[_Case], int]:
    """Every (case, role) to run, approved cases only, narrowed by ``cases`` and ``roles``; and how many were skipped."""
    jobs, skipped = [], 0
    for suite in suites:
        approved = suite.approved()
        chosen = select_cases(approved, cases) if cases else approved
        for case in chosen:
            for role, outcome in case.outcome.items():
                if roles and role not in roles:
                    continue
                if outcome == "clarify" and not clarify:
                    skipped += 1
                    continue
                jobs.append(_Case(suite.name, case, role))
    return jobs, skipped


def _publish_case(ctx: Any, record: Dict[str, Any]) -> None:
    bus = getattr(ctx, "bus", None)
    if bus is None:
        return
    status = record["status"]
    bus.publish("eval.case", EvalCaseEvent(
        suite=record["suite"], case_id=record["id"], role=record["role"],
        passed=True if status == "pass" else (None if status in ("error", "skip") else False),
        source=record["source"], scores={n: s["passed"] for n, s in record["scores"].items()},
        cost=record["cost"]))


def run_evals(
    ctx: Any,
    suites: Sequence[EvalSuite],
    *,
    mode: str = "full",
    llm: LLMConfigs = None,
    cases: Optional[Sequence[str]] = None,
    roles: Optional[Sequence[str]] = None,
    scorers: Sequence[str],
    curated: bool,
    passes: int = 1,
    max_cost_usd: Optional[float] = None,
    on_case: Optional[OnCase] = None,
    clarify: bool = False,
    prices: Dict[str, ModelPrice] = PRICES,
) -> EvalReport:
    """Runs every approved case of ``suites`` once per role in its ``outcome``, ``passes`` times.

    See the module docstring for the modes. ``scorers`` are registry names;
    those that cannot score ``mode`` are left out. ``on_case`` is called after
    each case with the config's name, the case record and the dollars spent
    so far.

    Raises:
        ValueError: an unknown mode, a ``cases`` selector matching nothing, mode
            full without ``max_cost_usd``, or mode plans with an answerable
            case that has no plan.
        UnknownPriceError: mode full with a model the price table cannot price.
        RegistryError: a scorer name that is not registered.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown mode '{mode}'. Modes: {', '.join(MODES)}.")
    jobs, skipped = _jobs(suites, cases, roles, clarify)
    built = {name: s for name, s in build_scorers(ctx.registry, list(scorers)).items() if mode in type(s).modes}
    report = EvalReport(mode=mode, roles=list(roles) if roles else None, passes=passes, clarify=clarify,
                        curated=bool(curated) and mode == "full", max_cost_usd=max_cost_usd, skipped=skipped,
                        suites=[{"name": s.name, "path": str(s.path) if s.path else "", "digest": s.digest()}
                                for s in suites])
    # Refusals are scored against the generic message; the engine's own config is never changed.
    base: Dict[str, Any] = {"policies.refusal_names_tables": False}
    if mode == "full":
        records = _run_full(ctx, jobs, built, report, base, llm, passes, max_cost_usd, on_case, prices)
    elif mode == "plans":
        records = {"plans": _run_plans(ctx.derive({**base, "curated": None}), jobs, built, passes, on_case)}
    else:
        run = ctx.derive({**base, **retrieval_recall.RETRIEVAL_CHANGES, "curated": None})
        report.settings = {"retrieval": retrieval_recall.retrieval_settings(run)} if hasattr(run, "vector_store") \
            else {}
        records = {"retrieval": _run_retrieval(run, jobs, built, passes, on_case)}

    for name, recs in records.items():
        board = report.configs.setdefault(name, {})
        board.update(score_config(recs, passes))
        board.setdefault("planned_cases", len(jobs) * passes)
        board["completed_cases"] = len(recs)
    if len(report.configs) > 1:
        report.comparison = compare(report.configs)
    bus = getattr(ctx, "bus", None)
    if bus is not None:
        bus.publish("eval.finished", EvalFinished(
            suite=",".join(s["name"] for s in report.suites), cases=sum(len(r) for r in records.values()),
            accuracy=report.accuracy, accuracy_generated=report.accuracy_generated, cost=round(report.spent, 6)))
    return report


def _finish(ctx: Any, name: str, record: Dict[str, Any], records: List[Dict[str, Any]], spent: float,
            on_case: Optional[OnCase]) -> None:
    records.append(record)
    _publish_case(ctx, record)
    if on_case:
        on_case(name, record, spent)


def _run_plans(run: Any, jobs: List[_Case], scorers: Dict[str, Scorer], passes: int,
               on_case: Optional[OnCase]) -> List[Dict[str, Any]]:
    cases = {id(j.case): j.case for j in jobs}.values()
    missing = sorted({c.id for c in cases if c.sql is not None and not c.plan})
    if missing:
        raise ValueError(f"Mode plans: no gold plan for {', '.join(missing)}. Add each case's plan.")
    records: List[Dict[str, Any]] = []
    with GoldPlanLLM(gold_plans(cases)) as llm:
        run.llm_registry.replace_llms({"default": llm.agent_config()})
        for pass_no in range(1, passes + 1):
            for job in jobs:
                llm.select(job.case)
                record, _ = _one(run, job, "plans", scorers, pass_no)
                _finish(run, "plans", record, records, 0.0, on_case)
    return records


def _run_retrieval(run: Any, jobs: List[_Case], scorers: Dict[str, Scorer], passes: int,
                   on_case: Optional[OnCase]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for pass_no in range(1, passes + 1):
        for job in jobs:
            t0 = time.perf_counter()
            sent, routed = _measure_retrieval(run, job.case, job.role)
            scored = _scored(job.case, job.role, "retrieval", scorers, QueryResult(), sent, routed)
            record = _record(job.suite, job.case, job.role, pass_no, scored, None, 0.0, time.perf_counter() - t0)
            _finish(run, "retrieval", record, records, 0.0, on_case)
    return records


def _one(run: Any, job: _Case, mode: str, scorers: Dict[str, Scorer], pass_no: int
         ) -> Tuple[Dict[str, Any], Optional[QueryResult]]:
    """Ask one case and score it; a run that raises is a failure, not an abort."""
    t0 = time.perf_counter()
    try:
        result, sent = _ask(run, job.case, job.role)
    except Exception as exc:  # one broken case must not abort the run
        reason = f"run raised {type(exc).__name__}: {exc}"
        scored = {"status": "fail", "reason": reason, "lenient_status": "fail", "scores": {}}
        record = _record(job.suite, job.case, job.role, pass_no, scored, None, 0.0, time.perf_counter() - t0)
        record["error_codes"] = ["EXCEPTION"]
        return record, None
    latency = time.perf_counter() - t0
    scored = _scored(job.case, job.role, mode, scorers, result, sent)
    return _record(job.suite, job.case, job.role, pass_no, scored, result, 0.0, latency), result


def _run_full(ctx: Any, jobs: List[_Case], scorers: Dict[str, Scorer], report: EvalReport, base: Dict[str, Any],
              llm: LLMConfigs, passes: int, max_cost_usd: Optional[float], on_case: Optional[OnCase],
              prices: Dict[str, ModelPrice]) -> Dict[str, List[Dict[str, Any]]]:
    if not max_cost_usd or max_cost_usd <= 0:
        raise ValueError("Mode full spends money: it needs a positive max_cost_usd (dollars), "
                         "in evals.yaml or as an argument.")
    configs = llm_configs(ctx, llm)
    check_prices(configs, prices)
    report.prices_checked_on = PRICES_CHECKED_ON
    changes = {**base, "settings.pipeline.plan_cache": False}
    spent, dearest = 0.0, 0.0
    records: Dict[str, List[Dict[str, Any]]] = {name: [] for name in configs}

    for name, cfg in configs.items():
        agents = {**(cfg.agents or {}), "default": cfg.default}
        run = ctx.derive({**changes, **({} if report.curated else {"curated": None})})
        run.llm_registry.replace_llms(agents)
        generated_run = None
        report.configs[name] = {
            "models": {LLM_AGENTS[n]: f"{a.provider}:{a.model}" for n, a in node_agents(cfg).items()},
            "planned_cases": len(jobs) * passes}
        for pass_no in range(1, passes + 1):
            for job in jobs:
                if spent + dearest > max_cost_usd:
                    report.stopped = "max_cost"
                    break
                record, result = _one(run, job, "full", scorers, pass_no)
                cost = question_cost(result.usage, cfg, prices) if result else 0.0
                if result is not None and result.source.type == "verified":
                    # D12: what the model alone would have answered, scored as the generated answer.
                    if generated_run is None:
                        generated_run = ctx.derive({**changes, "curated": None})
                        generated_run.llm_registry.replace_llms(agents)
                    second, second_result = _one(generated_run, job, "full", scorers, pass_no)
                    cost += question_cost(second_result.usage, cfg, prices) if second_result else 0.0
                    record["generated"] = {k: second[k] for k in ("status", "reason", "lenient_status", "scores",
                                                                  "sql")}
                record["cost"] = round(cost, 8)
                spent += cost
                dearest = max(dearest, cost)
                report.spent = round(spent, 6)
                _finish(ctx, name, record, records[name], spent, on_case)
            if report.stopped:
                break
        if report.stopped:
            break
    return records
