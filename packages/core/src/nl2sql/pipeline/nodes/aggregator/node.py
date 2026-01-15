from __future__ import annotations
from typing import List, Dict, Any, Literal, Callable, Union, TYPE_CHECKING
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from nl2sql.llm.registry import LLMRegistry

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from .schemas import AggregatedResponse
from .prompts import AGGREGATOR_PROMPT
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode

from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext

logger = get_logger("aggregator")



class AggregatorNode:
    """Node responsible for aggregating results from parallel sub-queries.

    This node synthesizes intermediate results from multiple execution branches.
    It can either pass through a single result directly (Fast Path) or use an LLM
    to generate a summary (Slow Path).

    Attributes:
        llm (ChatOpenAI): The language model callable used for aggregation.
        prompt (ChatPromptTemplate): The prompt template used for aggregation.
        chain (Runnable): The LangChain runnable sequence.
    """

    def __init__(self, ctx: NL2SQLContext):
        """Initializes the AggregatorNode.

        Args:
            registry (LLMRegistry): The LLM registry.
        """
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.llm = ctx.llm_registry.get_llm(self.node_name)
        self.prompt = ChatPromptTemplate.from_template(AGGREGATOR_PROMPT)
        self.chain = self.prompt | self.llm.with_structured_output(AggregatedResponse) 

    def _display_result_with_llm(self, state: GraphState) -> str:
        """Generates a text summary using the LLM.

        Args:
            state (GraphState): The current graph state containing intermediate results.

        Returns:
            str: A string containing the aggregated final answer.
        """
        user_query = state.user_query
        query_history = state.query_history or []
        formatted_results = ""

        for i, item in enumerate(query_history):
            ds_id = item.get("datasource_id", "unknown")
            sub_q = item.get("sub_query", "unknown")
            exec_data = item.get("execution")
            
            rows = []
            if exec_data:
                # Handle both Pydantic model and dict serialization
                if hasattr(exec_data, "rows"):
                    rows = exec_data.rows
                elif isinstance(exec_data, dict):
                    rows = exec_data.get("rows", [])
            
            formatted_results += f"--- Result {i+1} (Datasource: {ds_id}) ---\nSub-Query: {sub_q}\nData: {str(rows)}\n\n"

        if state.errors:
            formatted_results += "\n--- Errors Encountered ---\n"
            for err in state.errors:
                safe_msg = err.get_safe_message()
                formatted_results += f"Error from {err.node}: {safe_msg}\n"

        response: AggregatedResponse = self.chain.invoke({
            "user_query": user_query,
            "intermediate_results": formatted_results
        })

        final_answer = f"### Summary\n{response.summary}\n\n"
        if response.format_type == "table":
            final_answer += f"### Data\n\n{response.content}"
        elif response.format_type == "list":
            final_answer += f"### Details\n\n{response.content}"
        else:
            final_answer += f"\n{response.content}"

        return final_answer

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """Executes the aggregation logic.

        Determines whether to use the fast path (direct data return) or slow
        path (LLM synthesis) based on complexity and output mode.

        Args:
            state (GraphState): The current graph state.

        Returns:
            Dict[str, Any]: A dictionary containing the final answer and reasoning.
        """
        user_query = state.user_query
        query_history = state.query_history or []
        try:
            output_mode = state.output_mode

            if len(query_history) == 1 and not state.errors and output_mode == "data":
                item = query_history[0]
                exec_data = item.get("execution")
                rows = []
                if exec_data:
                    if hasattr(exec_data, "rows"):
                        rows = exec_data.rows
                    elif isinstance(exec_data, dict):
                        rows = exec_data.get("rows", [])

                return {
                    "final_answer": rows,
                    "reasoning": [{"node": self.node_name, "content": "Fast path: Raw data result passed through (output_mode='data')."}]
                }

            final_answer = self._display_result_with_llm(state)

            return {
                "final_answer": final_answer,
                "reasoning": [{"node": self.node_name, "content": "LLM Aggregation used."}]
            }
        except Exception as e:
            logger.error(f"Node {self.node_name} failed: {e}")
            return {
                "final_answer": f"Error during aggregation: {str(e)}",
                "reasoning": [{"node": self.node_name, "content": f"Error: {str(e)}", "type": "error"}],
                "errors": [
                    PipelineError(
                        node=self.node_name,
                        message=f"Aggregator failed: {str(e)}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.AGGREGATOR_FAILED,
                        stack_trace=str(e)
                    )
                ]
            }
