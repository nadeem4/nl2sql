from typing import List, Any, Dict, TYPE_CHECKING
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from .schemas import SemanticAnalysisResponse
from .prompts import SEMANTIC_ANALYSIS_PROMPT
from nl2sql.common.logger import get_logger

logger = get_logger("semantic_node")


class SemanticAnalysisNode:
    """Normalizes questions and extracts metadata (keywords, synonyms).

    Used by both Online Decomposer (Query Expansion) and Offline Indexing (Example Enrichment).

    Attributes:
        llm (Any): The language model to use for analysis.
        prompt (ChatPromptTemplate): The prompt template for analysis.
        chain (Runnable): The analysis chain.
    """

    def __init__(self, llm: Any):
        """Initializes the SemanticAnalysisNode.

        Args:
            llm (Any): The language model instance.
        """
        self.llm = llm
        self.prompt = ChatPromptTemplate.from_template(SEMANTIC_ANALYSIS_PROMPT)
        self.chain = self.prompt | self.llm

    def invoke(self, query: str) -> SemanticAnalysisResponse:
        """Analyzes the query and returns normalization + expansion data.

        Directly callable (used by Indexing).

        Args:
            query (str): The user query to analyze.

        Returns:
            SemanticAnalysisResponse: The analysis result including canonical form,
                keywords, and synonyms.
        """
        try:
            return self.chain.invoke({"user_query": query})
        except Exception as e:
            logger.error(f"Semantic Analysis failed: {e}")
            return SemanticAnalysisResponse(
                canonical_query=query,
                thought_process="Analysis failed, returning raw query.",
                keywords=[],
                synonyms=[],
                reasoning=f"Analysis failed: {str(e)}"
            )

    def __call__(self, state: "GraphState") -> Dict[str, Any]:
        """Executes the semantic analysis node within the LangGraph pipeline.

        Args:
            state (GraphState): The current graph state.

        Returns:
            Dict[str, Any]: A dictionary containing the 'semantic_analysis' result
                and 'reasoning'.
        """
        response = self.invoke(state.user_query)

        reasoning_step = {
            "node": "semantic_analysis",
            "content": response.reasoning,
            "metadata": {
                "keywords": response.keywords,
                "synonyms": response.synonyms
            }
        }

        return {
            "semantic_analysis": response,
            "reasoning": [reasoning_step]
        }
