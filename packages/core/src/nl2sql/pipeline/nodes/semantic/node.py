from typing import List, Any, Dict, TYPE_CHECKING
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from .schemas import SemanticAnalysisResponse
from .prompts import SEMANTIC_ANALYSIS_PROMPT
from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext

logger = get_logger("semantic_node")


class SemanticAnalysisNode:
    """Normalizes questions and extracts metadata (keywords, synonyms).

    Used by both Online Decomposer (Query Expansion) and Offline Indexing (Example Enrichment).

    Attributes:
        llm (Any): The language model to use for analysis.
        prompt (ChatPromptTemplate): The prompt template for analysis.
        chain (Runnable): The analysis chain.
    """

    def __init__(self, ctx: NL2SQLContext):
        """Initializes the SemanticAnalysisNode.

        Args:
            llm (Any): The language model instance.
        """
        self.node_name = self.__class__.__name__.lower().replace('node', '')
        self.llm = ctx.llm_registry.get_llm(self.node_name)
        self.prompt = ChatPromptTemplate.from_template(SEMANTIC_ANALYSIS_PROMPT)
        self.chain = self.prompt | self.llm.with_structured_output(SemanticAnalysisResponse)


    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """Executes the semantic analysis node within the LangGraph pipeline.

        Args:
            state (GraphState): The current graph state.

        Returns:
            Dict[str, Any]: A dictionary containing the 'semantic_analysis' result
                and 'reasoning'.
        """
        try:
            response = self.chain.invoke({"user_query": state.user_query})
        except Exception as e:
            logger.error(f"Node {self.node_name} failed: {e}")
            return {
                "reasoning": [{"node": self.node_name, "content": f"Semantic Analysis failed: {e}", "type": "error"}],
                "errors": [
                    PipelineError(
                        node=self.node_name,
                        message=f"Semantic Analysis failed: {e}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.SEMANTIC_ANALYSIS_FAILED,
                        stack_trace=str(e)
                    )
                ]
            }

        reasoning =[
            {
                "node": self.node_name,
                "content": response.reasoning,
            }
        ]

        return {
            "semantic_analysis": response,
            "reasoning": reasoning
        }
