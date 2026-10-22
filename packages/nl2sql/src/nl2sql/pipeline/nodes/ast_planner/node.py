from __future__ import annotations
import json
import traceback
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from langchain_core.runnables import Runnable

from .prompts import PLANNER_PROMPT, PLANNER_EXAMPLES
from .schemas import PlanAlternative, PlannerAnswer, PlanModel, ASTPlannerResponse
from nl2sql.knowledge.render import business_context_of
from nl2sql.pipeline.nodes.schema_retriever.schema import render_schema_for_prompt
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.common.dates import run_date
from nl2sql.hooks import HookVerdict, hooks_of
from nl2sql.policies import UserContext
from nl2sql.runtime import Runtime
from nl2sql.llm.failures import provider_error
from nl2sql.llm.wires import structured
from nl2sql.pipeline.plan_cache import plan_cache_for
from nl2sql.pipeline.retry_feedback import as_planner_feedback

if TYPE_CHECKING:
    from nl2sql.pipeline.state import SubgraphExecutionState

logger = get_logger("planner")


def semantic_context_of(sub_query: Any) -> str:
    """The sub-query's structured intent, as the planner's [SEMANTIC_CONTEXT].

    The intent text alone can drop what the decomposer said structurally, such
    as a ranking and a limit, so the planner gets the fields too.
    """
    if sub_query is None:
        return ""
    fields = {
        "metrics": [m.model_dump(exclude_none=True) for m in sub_query.metrics],
        "filters": [f.model_dump() for f in sub_query.filters],
        "group_by": [g.model_dump() for g in sub_query.group_by],
        "order_by": [o.model_dump() for o in sub_query.order_by],
        "limit": sub_query.limit,
    }
    fields = {k: v for k, v in fields.items() if v not in ([], None)}
    return json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str) if fields else ""


class ASTPlannerNode:
    """Generates a structured SQL execution plan: the planner's answer (``PlannerAnswer``).

    Uses an LLM to interpret the user query and semantic context, producing a
    deterministic Abstract Syntax Tree (AST) that represents the SQL query.

    Attributes:
        llm (Optional[Runnable]): The Language Model executable.
        chain (Optional[Runnable]): The langchain chain for planning.
    """

    def __init__(self, ctx: Runtime):
        """Initializes the PlannerNode.

        Args:
            ctx (Runtime): The context of the pipeline.
        """
        self.node_name = self.__class__.__name__.lower().replace('node', '')
        self.today = ctx.settings.pipeline.today
        # Plans are read here and written by the sub-query wrapper once one has
        # validated and executed (nl2sql.pipeline.graph_utils).
        self.plan_cache = plan_cache_for(ctx)
        self.hooks = hooks_of(ctx)
        self.llm = ctx.llm_registry.get_llm(self.node_name)

        self.prompt = PLANNER_PROMPT
        self.chain = self.prompt | structured(self.llm, PlannerAnswer)

    def _cached(self, state: SubgraphExecutionState) -> Optional[Dict[str, Any]]:
        """The update for a cache hit, or None to ask the model.

        Only a first attempt reads the cache: a retry means this sub-query's
        plan was just rejected, and serving the cached one again would loop.
        """
        if state.retry_count or state.errors:
            return None
        plan = self.plan_cache.get(state.sub_query)
        if plan is None:
            return None
        logger.info("Plan served from the plan cache; it is validated again before use.")
        return self.after_plan(state, plan, "cache", [
            "Plan served from the plan cache (no planner LLM call); it is validated again.",
            f"Tables: {', '.join(t.name for t in plan.tables)}",
        ])

    def check_plan(self, state: SubgraphExecutionState, plan: PlanModel) -> HookVerdict:
        """``plan.after`` on one plan of this sub-query: what the hooks let through, or what stopped it."""
        sub_query = state.sub_query
        return self.hooks.call(
            "plan.after", trace_id=state.trace_id, question=getattr(state, "user_query", "") or "",
            user=state.user_context or UserContext(), datasource_id=sub_query.datasource_id if sub_query else None,
            sub_query=sub_query, plan=plan)

    def checked_alternatives(self, state: SubgraphExecutionState,
                             alternatives: List[PlanAlternative]) -> List[PlanAlternative]:
        """Each alternative reading as ``plan.after`` lets it through: a denied one is dropped, a changed one kept
        as changed (wave_b.md, section 7). The clarifier validates what is left."""
        kept = []
        for alternative in alternatives:
            verdict = self.check_plan(state, alternative.plan)
            if verdict.allowed:
                kept.append(alternative.model_copy(update={"plan": verdict.ctx.plan}) if verdict.modified
                            else alternative)
        return kept

    def after_plan(self, state: SubgraphExecutionState, plan: PlanModel, plan_source: str,
                   reasoning: Optional[List[str]] = None) -> Dict[str, Any]:
        """The node's update for ``plan``, once ``plan.after`` has seen it.

        Every plan the validator gets passes here: the planner's, a cached
        one, and a plan a caller supplies (an approved or chosen plan). The
        response keeps ``plan`` as it came (``planner_plan``, which the plan
        cache stores) beside what the hooks let through. A deny leaves no plan
        and an error that is never retried.
        """
        verdict = self.check_plan(state, plan)
        notes = list(reasoning or [])
        if not verdict.allowed:
            return {
                "ast_planner_response": ASTPlannerResponse(plan=None, planner_plan=plan, plan_source=plan_source),
                "errors": [verdict.error(self.node_name)],
                "reasoning": [{"node": self.node_name, "content": notes + [
                    f"The plan.after hook '{verdict.hook}' stopped the plan."]}],
            }
        if verdict.modified:
            notes.append("A plan.after hook changed the plan; the changed plan is validated.")
        return {
            "ast_planner_response": ASTPlannerResponse(plan=verdict.ctx.plan if verdict.modified else plan,
                                                       planner_plan=plan,
                                                       plan_source=plan_source),
            "reasoning": [{"node": self.node_name, "content": notes}],
        }

    def __call__(self, state: SubgraphExecutionState) -> Dict[str, Any]:
        """Executes the planning node.

        Args:
           state (GraphState): The current state of the execution graph.

        Returns:s
            Dict[str, Any]: A dictionary containing the generated 'plan', 'reasoning',
                and any 'errors' encountered.
        """
        try:
            pinned = getattr(state, "pinned_plan", None)
            if pinned is not None:
                # A supplied, approved or chosen plan: no model call, and never the cache's.
                source = state.pinned_source.value if state.pinned_source is not None else "supplied"
                return self.after_plan(state, pinned, source, [
                    f"The plan is pinned ({source}); no planner LLM call. It is validated like any other.",
                    f"Tables: {', '.join(t.name for t in pinned.tables)}",
                ])
            cached = self._cached(state)
            if cached is not None:
                return cached

            relevant_tables = render_schema_for_prompt(state.relevant_tables)

            feedback = as_planner_feedback(state.errors)

            query_text = state.sub_query.intent if state.sub_query else ""
            semantic_context = semantic_context_of(state.sub_query)
            expected_schema = []
            if state.sub_query and state.sub_query.expected_schema:
                # join_key is the engine's flag for the validator, not part of the contract the planner meets.
                expected_schema = [c.model_dump(exclude={"join_key"}) for c in state.sub_query.expected_schema]
            answer: Optional[PlannerAnswer] = self.chain.invoke(
                {
                    "relevant_tables": relevant_tables,
                    "examples": PLANNER_EXAMPLES,
                    "feedback": feedback,
                    "expected_schema": expected_schema,
                    "semantic_context": semantic_context,
                    "business_context": business_context_of(state),
                    "user_query": query_text,
                    "today": run_date(self.today).isoformat(),
                }
            )
            if answer is None:
                # Structured output returns None when the model does not call
                # the tool; that is a failed plan, not a crash.
                logger.error("Planner returned no plan.")
                return {
                    "ast_planner_response": ASTPlannerResponse(plan=None),
                    "errors": [
                        PipelineError(
                            node=self.node_name,
                            message="Planner returned no plan.",
                            severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.PLANNING_FAILURE,
                        )
                    ],
                }

            plan = answer.plan
            notes = [f"Reasoning: {plan.reasoning or 'None'}", f"Tables: {', '.join(t.name for t in plan.tables)}"]
            if answer.alternatives:
                notes.append(f"The planner read '{answer.ambiguous_phrase or ''}' more than one way: "
                             f"{len(answer.alternatives)} other reading(s).")
            update = self.after_plan(state, plan, "llm", notes)
            response = update["ast_planner_response"]
            if answer.alternatives or answer.ambiguous_phrase:
                update["ast_planner_response"] = response.model_copy(update={
                    "ambiguous_phrase": answer.ambiguous_phrase,
                    "alternatives_offered": list(answer.alternatives),
                    "alternatives": (self.checked_alternatives(state, answer.alternatives)
                                     if response.plan is not None else [])})
            return update

        except Exception as exc:
            logger.exception("Planner failed")
            return {
                "ast_planner_response": ASTPlannerResponse(plan=None),
                "errors": [
                    provider_error(self.node_name, exc) or PipelineError(
                        node=self.node_name,
                        message="Planner failed.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.PLANNING_FAILURE,
                        stack_trace=traceback.format_exc(),
                    )
                ],
            }
