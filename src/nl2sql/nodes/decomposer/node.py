from typing import List, Dict, Any, Callable, Union
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

from nl2sql.schemas import GraphState, DecomposerResponse
from nl2sql.nodes.decomposer.prompts import DECOMPOSER_PROMPT

LLMCallable = Union[Callable[[str], Any], Runnable]

class DecomposerNode:
    """
    Node responsible for decomposing a complex query into sub-queries.
    """
    def __init__(self, llm: LLMCallable):
        self.llm = llm
        self.prompt = ChatPromptTemplate.from_template(DECOMPOSER_PROMPT)
        self.chain = self.prompt | self.llm

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        user_query = state.user_query
        
        try:
            response: DecomposerResponse = self.chain.invoke({"user_query": user_query})
            return {
                "sub_queries": response.sub_queries,
                "thoughts": {"decomposer": [response.reasoning]}
            }
        except Exception as e:
            # Fallback: return original query
            return {
                "sub_queries": [user_query],
                "thoughts": {"decomposer": [f"Error during decomposition: {str(e)}. Proceeding with original query."]}
            }
