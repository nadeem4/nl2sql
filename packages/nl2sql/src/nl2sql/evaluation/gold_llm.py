"""The gold-plan fake LLM: mode ``plans`` runs every model step on it, with no key.

A local ``FakeLLMServer`` stands in for every LLM node and answers for the
case last selected: the resolver's answerability check names the case's
datasources (none for a case with no SQL), the decomposer returns one
sub-query whose expected columns are the plan's select aliases, the planner
returns the case's hand-written plan, and the answer synthesizer a fixed
sentence. Everything else is the real pipeline -- the resolver's role check
and refusal, schema retrieval, the logical validator (RBAC included), the
generator and the executor -- so a failure here is a bug in a code node,
never in a model.

Every answer comes from :data:`RESPONSES`, keyed by the structured output's
schema name, the name the client asks the server for. A new structured
output is one more entry there: Wave B's planner answer, a wrapper around
``PlanModel``, is ``"PlannerAnswer": lambda llm: {"plan": llm.plan_payload()}``
(with its own field name).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional

from nl2sql.evaluation.suite import EvalCase
from nl2sql.llm.models import AgentConfig
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.testing.fake_llm import FakeLLMServer, Rule

ANSWER = {"summary": "Gold plan answer.", "format_type": "text", "content": "Gold plan answer.", "warnings": []}


def gold_plans(cases: Iterable[EvalCase]) -> Dict[str, PlanModel]:
    """Each case's hand-written plan, validated, keyed by case id."""
    return {c.id: PlanModel.model_validate(c.plan) for c in cases if c.plan}


def answerability(case: EvalCase) -> Dict[str, Any]:
    """The resolver's verdict: the case's own datasources when it has SQL, none otherwise.

    Naming the case's datasources rather than whatever the engine has
    registered keeps the run on the database the case is about, however
    many others sit beside it.
    """
    if case.sql is None:
        return {"answerable_datasource_ids": [], "reason": "Gold: no datasource holds this."}
    return {"answerable_datasource_ids": list(case.datasources),
            "reason": f"Gold: answerable from {', '.join(case.datasources)}."}


def decomposition(case: EvalCase, plan: PlanModel) -> Dict[str, Any]:
    """One standalone sub-query on the case's datasource, whose expected columns are the plan's aliases.

    The validator requires the plan's select aliases to equal the sub-query's
    ``expected_schema`` names, so they are derived from the plan.
    """
    return {
        "sub_queries": [{
            "id": "sq1", "datasource_id": case.datasources[0], "intent": case.question,
            "metrics": [], "filters": [], "group_by": [],
            "expected_schema": [{"name": s.alias} for s in plan.select_items],
        }],
        "combine_groups": [{"group_id": "g1", "operation": "standalone",
                            "inputs": [{"subquery_id": "sq1"}], "join_keys": []}],
        "post_combine_ops": [], "unmapped_subqueries": [],
    }


# Structured-output schema name -> the answer, given the fake (and so its current case).
RESPONSES: Dict[str, Callable[["GoldPlanLLM"], Any]] = {
    "AnswerabilityResponse": lambda llm: answerability(llm.case),
    "DecomposerResponse": lambda llm: decomposition(llm.case, llm.plan()),
    "PlanModel": lambda llm: llm.plan_payload(),
    "AggregatedResponse": lambda llm: ANSWER,
    # The refiner: the planner then re-serves the same plan, so a rejected plan stays rejected.
    "plain": lambda llm: "Keep the same plan.",
}


class GoldPlanLLM:
    """A fake LLM that answers for whichever case was last selected.

    Cases run one at a time, so one server serves them all: ``select`` sets
    the current one. ``responses`` starts as a copy of :data:`RESPONSES`; a
    test may replace an entry on one fake.
    """

    def __init__(self, plans: Dict[str, PlanModel]):
        self.plans = plans
        self.case: Optional[EvalCase] = None
        self.responses: Dict[str, Callable[[GoldPlanLLM], Any]] = dict(RESPONSES)
        self.server = FakeLLMServer([Rule(name, self._answerer(name)) for name in RESPONSES])

    def _answerer(self, name: str) -> Callable[[str], Any]:
        return lambda _prompt: self.respond(name)

    def respond(self, schema: str) -> Any:
        """The answer to a request for ``schema``, for the current case."""
        return self.responses[schema](self)

    def plan(self) -> PlanModel:
        case_id = getattr(self.case, "id", None)
        if case_id not in self.plans:
            raise KeyError(f"no gold plan for {case_id}")
        return self.plans[case_id]

    def plan_payload(self) -> Dict[str, Any]:
        return self.plan().model_dump(mode="json", exclude_none=True)

    def select(self, case: EvalCase) -> None:
        self.case = case

    def __enter__(self) -> "GoldPlanLLM":
        self.server.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.server.stop()

    def agent_config(self) -> AgentConfig:
        # The model name only has to be one the OpenAI client handles as usual.
        return AgentConfig(provider="openai", model="gpt-4o", base_url=self.server.base_url,
                           api_key="sk-fake-gold-plans")
