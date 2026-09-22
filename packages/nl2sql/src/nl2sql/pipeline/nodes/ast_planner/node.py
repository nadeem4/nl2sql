from __future__ import annotations
import traceback
from typing import Any, Dict, Optional, TYPE_CHECKING
from langchain_core.runnables import Runnable

from .prompts import PLANNER_PROMPT, PLANNER_EXAMPLES, dialect_notes
from .schemas import PlanModel, ASTPlannerResponse
from nl2sql.pipeline.nodes.schema_retriever.schema import render_schema_for_prompt
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext
from nl2sql.llm.wires import structured
from nl2sql.pipeline.plan_cache import PlanCache

if TYPE_CHECKING:
    from nl2sql.pipeline.state import SubgraphExecutionState

logger = get_logger("planner")


class ASTPlannerNode:
    """Generates a structured SQL execution plan (PlanModel).

    Uses an LLM to interpret the user query and semantic context, producing a
    deterministic Abstract Syntax Tree (AST) that represents the SQL query.

    Attributes:
        llm (Optional[Runnable]): The Language Model executable.
        chain (Optional[Runnable]): The langchain chain for planning.
    """

    def __init__(self, ctx: NL2SQLContext):
        """Initializes the PlannerNode.

        Args:
            ctx (NL2SQLContext): The context of the pipeline.
        """
        self.node_name = self.__class__.__name__.lower().replace('node', '')
        # Plans are read here and written by the sub-query wrapper once one has
        # validated and executed (nl2sql.pipeline.graph_utils).
        self.plan_cache = PlanCache(getattr(ctx, "schema_store", None))
        self.llm = ctx.llm_registry.get_llm(self.node_name)
        self.ds_registry = getattr(ctx, "ds_registry", None)

        self.prompt = PLANNER_PROMPT
        self.chain = self.prompt | structured(self.llm, PlanModel)

    def _dialect(self, datasource_id: Optional[str]) -> Optional[str]:
        """The SQL dialect of the datasource being planned for, or None when unknown."""
        if not datasource_id or self.ds_registry is None:
            return None
        try:
            return self.ds_registry.get_dialect(datasource_id)
        except Exception:
            logger.warning("No dialect for datasource %s; planning without one.", datasource_id)
            return None

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
        return {
            "ast_planner_response": ASTPlannerResponse(plan=plan, plan_source="cache"),
            "reasoning": [
                {
                    "node": self.node_name,
                    "content": [
                        "Plan served from the plan cache (no planner LLM call); it is validated again.",
                        f"Tables: {', '.join(t.name for t in plan.tables)}",
                    ],
                }
            ],
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
            cached = self._cached(state)
            if cached is not None:
                return cached

            relevant_tables = render_schema_for_prompt(state.relevant_tables)

            feedback = ""
            if state.errors:
                feedback = "\n".join(e.model_dump_json(exclude_none=True) for e in state.errors)

            query_text = state.sub_query.intent if state.sub_query else ""
            expected_schema = []
            if state.sub_query and state.sub_query.expected_schema:
                expected_schema = [c.model_dump() for c in state.sub_query.expected_schema]
            plan: PlanModel = self.chain.invoke(
                {
                    "dialect_notes": dialect_notes(self._dialect(state.sub_query.datasource_id if state.sub_query else None)),
                    "relevant_tables": relevant_tables,
                    "examples": PLANNER_EXAMPLES,
                    "feedback": feedback,
                    "expected_schema": expected_schema,
                    "semantic_context": "",
                    "user_query": query_text,
                }
            )
            if plan is None:
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

            return {
                "ast_planner_response": ASTPlannerResponse(plan=plan),
                "reasoning": [
                    {
                        "node": self.node_name,
                        "content": [
                            f"Reasoning: {plan.reasoning or 'None'}",
                            f"Tables: {', '.join(t.name for t in plan.tables)}",
                        ],
                    }
                ],
            }

        except Exception as exc:
            logger.exception("Planner failed")
            return {
                "ast_planner_response": ASTPlannerResponse(plan=None),
                "errors": [
                    PipelineError(
                        node=self.node_name,
                        message="Planner failed.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.PLANNING_FAILURE,
                        stack_trace=traceback.format_exc(),
                    )
                ],
            }
