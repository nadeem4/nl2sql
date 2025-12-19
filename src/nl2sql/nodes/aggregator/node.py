from __future__ import annotations
from typing import List, Dict, Any, Literal, Callable, Union, TYPE_CHECKING
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

if TYPE_CHECKING:
    from nl2sql.schemas import GraphState

from .schemas import AggregatedResponse
from nl2sql.nodes.aggregator.prompts import AGGREGATOR_PROMPT
from nl2sql.errors import PipelineError, ErrorSeverity, ErrorCode

from nl2sql.logger import get_logger

logger = get_logger("aggregator")

LLMCallable = Union[Callable[[str], Any], Runnable]


    

class AggregatorNode:
    """
    Node responsible for aggregating results from parallel sub-queries.
    """
    def __init__(self, llm: LLMCallable):
        self.llm = llm
        self.prompt = ChatPromptTemplate.from_template(AGGREGATOR_PROMPT)
        self.chain = self.prompt | self.llm




    def _display_result_with_llm(self, state: GraphState):
        """
        Display result with LLM aggregation.
        """
        user_query = state.user_query
        intermediate_results = state.intermediate_results or []
        formatted_results = ""
        for i, res in enumerate(intermediate_results):
            formatted_results += f"--- Result {i+1} ---\n{str(res)}\n\n"
        
        if state.errors:
            formatted_results += "\n--- Errors Encountered ---\n"
            for err in state.errors:
                formatted_results += f"Error from {err.node}: {err.message}\n"
            
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
        user_query = state.user_query
        intermediate_results = state.intermediate_results or []
        node_name = "aggregator"
        
        try:
            if len(intermediate_results) == 1 and not state.errors and state.sub_queries[0].complexity == "simple":
                # Fast path: Skip LLM, return None for final_answer so run.py uses execution result
                return {
                    "final_answer": None,
                    "reasoning": [{"node": "aggregator", "content": "Fast path: Execution result used directly."}]
                }
            
            final_answer = self._display_result_with_llm(state)
            
            return {
                "final_answer": final_answer,
                "reasoning": [{"node": "aggregator", "content": "LLM Aggregation used."}]
            }
        except Exception as e:
            logger.error(f"Node {node_name} failed: {e}")
            return {
                "final_answer": f"Error during aggregation: {str(e)}",
                "reasoning": [{"node": "aggregator", "content": f"Error: {str(e)}", "type": "error"}],
                "errors": [
                    PipelineError(
                        node=node_name,
                        message=f"Aggregator failed: {str(e)}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.AGGREGATOR_FAILED,
                        stack_trace=str(e)
                    )
                ]
            }
