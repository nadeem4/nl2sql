"""Tier 1 evaluation: hand-written gold plans through the code nodes, no key.

Each answerable gold question has a hand-written ``PlanModel`` in
``datasets/chinook_gold_plans.yaml``. A local ``FakeLLMServer`` stands in for
every LLM node: the resolver's answerability check gets the question's own
datasource (``["chinook"]`` for this set) for a question with gold SQL and
``[]`` for an unanswerable one, the decomposer gets one sub-query whose
expected columns are the plan's select aliases, the planner gets the gold
plan, and the answer synthesizer a fixed sentence.
Everything else is the real pipeline -- the resolver's role check and
refusal, schema retrieval, the logical validator (including RBAC), the SQL
generator and the executor against Chinook -- and each case is scored per
role: rows against ``gold_result``, a refusal against the generic
``SECURITY_VIOLATION``, an unanswerable question against
``QUESTION_NOT_ANSWERABLE``. A failure here is a bug in a code node, never in
a model.
"""
from __future__ import annotations

import pathlib
from typing import Any, Dict, Optional

import yaml

from nl2sql.common.settings import settings
from nl2sql.configs.llm import AgentConfig
from nl2sql.context import NL2SQLContext
from nl2sql.evaluation.benchmark_runner import BenchmarkResult, BenchmarkRunner
from nl2sql.evaluation.gold import GoldQuestion, load_gold_dataset
from nl2sql.evaluation.types import BenchmarkConfig
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.testing.fake_llm import FakeLLMServer, Rule

GOLD_PLANS_PATH = pathlib.Path(__file__).parent / "datasets" / "chinook_gold_plans.yaml"

ANSWER = {"summary": "Gold plan answer.", "format_type": "text", "content": "Gold plan answer.", "warnings": []}


def load_gold_plans(path: pathlib.Path = GOLD_PLANS_PATH) -> Dict[str, PlanModel]:
    """Loads the gold plans, keyed by question id."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {qid: PlanModel.model_validate(plan) for qid, plan in raw.items()}


def answerability_response(question: GoldQuestion) -> Dict[str, Any]:
    """The resolver's verdict: the question's own datasource when it has gold SQL, none otherwise.

    Naming the gold entry's datasource rather than whatever the context has
    registered keeps the run on the database the question is about, however
    many others sit beside it.
    """
    if question.gold_sql is None:
        return {"answerable_datasource_ids": [], "reason": "Gold: no datasource holds this."}
    return {"answerable_datasource_ids": [question.datasource],
            "reason": f"Gold: answerable from {question.datasource}."}


def decomposer_response(question: GoldQuestion, plan: PlanModel) -> Dict[str, Any]:
    """One standalone sub-query on the question's datasource, whose expected columns are the plan's aliases.

    The validator requires the plan's select aliases to equal the sub-query's
    ``expected_schema`` names, so they are derived from the plan.
    """
    return {
        "sub_queries": [{
            "id": "sq1", "datasource_id": question.datasource, "intent": question.question,
            "metrics": [], "filters": [], "group_by": [],
            "expected_schema": [{"name": s.alias} for s in plan.select_items],
        }],
        "combine_groups": [{"group_id": "g1", "operation": "standalone",
                            "inputs": [{"subquery_id": "sq1"}], "join_keys": []}],
        "post_combine_ops": [], "unmapped_subqueries": [],
    }


class GoldPlanLLM:
    """A fake LLM that answers for whichever gold question was last selected.

    Runs are sequential, so one server serves every question: ``select`` sets
    the current one. The refiner gets "Keep the same plan." and the planner
    then re-serves the same gold plan, so a rejected plan stays rejected.
    """

    def __init__(self, plans: Dict[str, PlanModel]):
        self.plans = plans
        self._question: Optional[GoldQuestion] = None
        self.server = FakeLLMServer([
            Rule("AnswerabilityResponse", lambda _text: answerability_response(self._question)),
            Rule("DecomposerResponse", lambda _text: decomposer_response(self._question, self._plan())),
            Rule("PlanModel", lambda _text: self._plan().model_dump(mode="json", exclude_none=True)),
            Rule("AggregatedResponse", ANSWER),
            Rule("plain", "Keep the same plan."),
        ])

    def _plan(self) -> PlanModel:
        if self._question is None or self._question.id not in self.plans:
            raise KeyError(f"no gold plan for {getattr(self._question, 'id', None)}")
        return self.plans[self._question.id]

    def select(self, question: GoldQuestion) -> None:
        self._question = question

    def __enter__(self) -> "GoldPlanLLM":
        self.server.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.server.stop()

    def agent_config(self) -> AgentConfig:
        # The model name only has to be one the OpenAI client handles as usual.
        return AgentConfig(provider="openai", model="gpt-4o", base_url=self.server.base_url,
                           api_key="sk-fake-tier1")


def run_tier1(ctx: NL2SQLContext, config: Optional[BenchmarkConfig] = None) -> BenchmarkResult:
    """Runs every selected gold question once per role on ``ctx``, key-free.

    ``ctx`` must hold the indexed Chinook demo datasource. Its LLM registry is
    replaced with the gold-plan fake for the duration of the run, so the
    context should not be reused for real questions afterwards.
    """
    config = (config or BenchmarkConfig()).model_copy(update={"iterations": 1})
    plans = load_gold_plans()
    missing = [q.id for q in load_gold_dataset(config.dataset_path) if q.gold_sql and q.id not in plans]
    if missing:
        raise ValueError(f"No gold plan for answerable question(s): {', '.join(missing)}")

    # Refusals are scored against the default, generic message. The demo opts
    # into naming the forbidden table (RBAC_REFUSAL_NAMES_TABLES=true in
    # .env.demo), so the default is pinned for the run and restored after.
    names_tables = settings.rbac_refusal_names_tables
    settings.rbac_refusal_names_tables = False
    try:
        with GoldPlanLLM(plans) as llm:
            ctx.llm_registry.replace_llms({"default": llm.agent_config()})
            runner = BenchmarkRunner(config, ctx, workers=1, before_case=llm.select)
            return runner.run_dataset()
    finally:
        settings.rbac_refusal_names_tables = names_tables
