from __future__ import annotations
from typing import List, Dict, Any, Literal, Callable, Union, TYPE_CHECKING
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

if TYPE_CHECKING:
    from nl2sql.schemas import GraphState

from .schemas import AggregatedResponse
from .schemas import AggregatedResponse
from nl2sql.nodes.aggregator.prompts import AGGREGATOR_PROMPT

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

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        user_query = state.user_query
        intermediate_results = state.intermediate_results or []
        node_name = "aggregator"
        
        try:
            # Format intermediate results for the prompt
            formatted_results = ""
            for i, res in enumerate(intermediate_results):
                formatted_results += f"--- Result {i+1} ---\n{str(res)}\n\n"
                
            response: AggregatedResponse = self.chain.invoke({
                "user_query": user_query,
                "intermediate_results": formatted_results
            })
            
            # Construct the final answer string
            final_answer = f"### Summary\n{response.summary}\n\n"
            if response.format_type == "table":
                final_answer += f"### Data\n\n{response.content}"
            elif response.format_type == "list":
                final_answer += f"### Details\n\n{response.content}"
            else:
                final_answer += f"\n{response.content}"
            
            return {
                "final_answer": final_answer,
                "reasoning": [{"node": "aggregator", "content": f"Chosen format: {response.format_type}"}]
            }
        except Exception as e:
            logger.error(f"Node {node_name} failed: {e}")
            return {
                "final_answer": f"Error during aggregation: {str(e)}",
                "reasoning": [{"node": "aggregator", "content": f"Error: {str(e)}", "type": "error"}]
            }
