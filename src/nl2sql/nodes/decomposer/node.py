from __future__ import annotations
from typing import List, Dict, Any, Callable, Union, TYPE_CHECKING
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

if TYPE_CHECKING:
    from nl2sql.schemas import GraphState

from .schemas import DecomposerResponse
from nl2sql.nodes.decomposer.prompts import DECOMPOSER_PROMPT
from nl2sql.datasource_registry import DatasourceRegistry

from nl2sql.logger import get_logger



logger = get_logger("decomposer")

LLMCallable = Union[Callable[[str], Any], Runnable]

class DecomposerNode:
    """
    Node responsible for decomposing a complex query into sub-queries.
    """
    def __init__(self, llm: LLMCallable, registry: DatasourceRegistry):
        self.llm = llm
        self.registry = registry
        self.prompt = ChatPromptTemplate.from_template(DECOMPOSER_PROMPT)
        self.chain = self.prompt | self.llm

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        user_query = state.user_query
        node_name = "decomposer"

        try:
            profiles = self.registry.list_profiles()
            datasources_str = "\n".join([f"- {p.id}: {p.description}" for p in profiles])

            response: DecomposerResponse = self.chain.invoke({
                "user_query": user_query,
                "datasources": datasources_str
            })

            return {
                "sub_queries": response.sub_queries,
                "reasoning": [{"node": "decomposer", "content": response.reasoning}]
            }
        except Exception as e:
            return {
                "sub_queries": [user_query],
                "reasoning": [{"node": "decomposer", "content": f"Error during decomposition: {str(e)}. Proceeding with original query.", "type": "error"}]
            }
