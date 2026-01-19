from __future__ import annotations
from typing import Dict, Any, TYPE_CHECKING
import json

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from nl2sql.llm.registry import LLMRegistry

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from .schemas import ResultPlan
from .prompts import GLOBAL_PLANNER_PROMPT
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext

logger = get_logger("global_planner")


class GlobalPlannerNode:
    """Generates a deterministic execution plan for aggregating sub-query results.
    
    This node runs AFTER the Decomposer and BEFORE the SQL Agents.
    It does not execute SQL, but produces a blueprint (ResultPlan) for the Aggregator.
    """

    def __init__(self, ctx: NL2SQLContext):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.llm = ctx.llm_registry.get_llm(self.node_name) 
        self.prompt = ChatPromptTemplate.from_template(GLOBAL_PLANNER_PROMPT)
        self.chain = self.prompt | self.llm.with_structured_output(ResultPlan)

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """Executes the planning logic."""
        sub_queries = state.sub_queries or []

        # Optimization removed to enforce strict ResultPlan generation for all queries.


        try:
            # Serialize SubQueries for the LLM
            sq_json_list = []
            for sq in sub_queries:
                sq_dict = sq.model_dump(include={"id", "query", "datasource_id", "expected_schema"})
                sq_json_list.append(sq_dict)
            
            sub_queries_json = json.dumps(sq_json_list, indent=2)

            plan: ResultPlan = self.chain.invoke({
                "user_query": state.user_query,
                "sub_queries_json": sub_queries_json
            })

            return {
                "result_plan": plan,
                "reasoning": [{"node": self.node_name, "content": f"Generated plan with {len(plan.steps)} steps."}]
            }

        except Exception as e:
            logger.error(f"GlobalPlanner failed: {e}")
            return {
                "result_plan": None,
                "reasoning": [{"node": self.node_name, "content": f"Planning failed: {e}", "type": "error"}],
                "errors": [
                    PipelineError(
                        node=self.node_name,
                        message=f"Global Plan generation failed: {str(e)}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.PLANNER_FAILED 
                    )
                ]
            }
