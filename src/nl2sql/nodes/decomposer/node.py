from __future__ import annotations
from typing import List, Dict, Any, Callable, Union, TYPE_CHECKING, Optional
import json
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

if TYPE_CHECKING:
    from nl2sql.schemas import GraphState
    from nl2sql.vector_store import OrchestratorVectorStore

from .schemas import DecomposerResponse
from nl2sql.nodes.decomposer.prompts import DECOMPOSER_PROMPT
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.errors import PipelineError, ErrorSeverity

from nl2sql.logger import get_logger
from nl2sql.agents import canonicalize_query
from nl2sql.schemas import SubQuery

logger = get_logger("decomposer")

LLMCallable = Union[Callable[[str], Any], Runnable]

class DecomposerNode:
    """
    Node responsible for decomposing a complex query into sub-queries.
    """
    def __init__(self, llm: LLMCallable, registry: DatasourceRegistry, vector_store: Optional[OrchestratorVectorStore] = None):
        self.llm = llm
        self.registry = registry
        self.vector_store = vector_store
        self.prompt = ChatPromptTemplate.from_template(DECOMPOSER_PROMPT)
        self.chain = self.prompt | self.llm.with_structured_output(DecomposerResponse)

    def _retrieve_context(self, query: str) -> str:
        """
        Retrieves relevant context (tables/schemas) using L1/L2 search strategies.
        Returns a formatted string for the prompt.
        """
        if not self.vector_store:
            return "No schema vector store available. Rely on general descriptions."
        
        try:
            results = self.vector_store.retrieve_routing_context(query, k=10)
        except Exception:
            return "Vector search failed. Rely on general descriptions."
            
        if not results:
            return "No specific tables found in vector index."

        cands = {}
        for res in results:
            ds = res.metadata.get("datasource_id")
            if not ds: continue
            
            if ds not in cands:
                cands[ds] = {"datasource_id": ds, "tables": [], "examples": []}
            
            if tbl := res.metadata.get("table_name"):
                 if tbl not in cands[ds]["tables"]:
                     cands[ds]["tables"].append(tbl)
            elif res.metadata.get("type", "table") == "example":
                 if res.page_content not in cands[ds]["examples"]:
                     cands[ds]["examples"].append(res.page_content)

        return json.dumps(list(cands.values()), indent=2)

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        user_query = state.user_query
        node_name = "decomposer"

        try:
            canonical_query = canonicalize_query(user_query, self.llm)
            logger.info(f"Canonicalized: {user_query} -> {canonical_query}")

            context_str = self._retrieve_context(canonical_query)
            
            profiles = self.registry.list_profiles()
            datasources_str = "\n".join([f"- {p.id}: {p.description}" for p in profiles])

            response: DecomposerResponse = self.chain.invoke({
                "user_query": user_query,
                "datasources": datasources_str,
                "schema_context": context_str
            })

            
            
            final_subqueries = []
            for sq in response.sub_queries:
                final_subqueries.append(SubQuery(
                    query=sq.query,
                    datasource_id=sq.datasource_id,
                    candidate_tables=sq.candidate_tables,
                    reasoning=sq.reasoning
                ))

            return {
                "sub_queries": final_subqueries, 
                "reasoning": [{"node": "decomposer", "content": response.reasoning}]
            }
        except Exception as e:
            return {
                "sub_queries": [], 
                "reasoning": [{"node": "decomposer", "content": f"Decomposition Crash: {str(e)}", "type": "error"}],
                "errors": [
                    PipelineError(
                        node=node_name,
                        message=f"Decomposition/Orchestration failed: {str(e)}",
                        severity=ErrorSeverity.CRITICAL, 
                        error_code="ORCHESTRATOR_CRASH",
                        stack_trace=str(e)
                    )
                ]
            }
