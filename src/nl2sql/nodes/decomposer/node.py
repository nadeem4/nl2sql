from __future__ import annotations
from typing import List, Dict, Any, Callable, Union, TYPE_CHECKING, Optional
import json
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

if TYPE_CHECKING:
    from nl2sql.schemas import GraphState
    from nl2sql.vector_store import OrchestratorVectorStore

from .schemas import DecomposerResponse
from nl2sql.nodes.decomposer.prompts import DECOMPOSER_PROMPT, INTENT_ENRICHER_PROMPT, CANONICALIZATION_PROMPT

from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.errors import PipelineError, ErrorSeverity, ErrorCode

from nl2sql.logger import get_logger
from nl2sql.nodes.decomposer.schemas import SubQuery
from nl2sql.llm_registry import LLMRegistry

logger = get_logger("decomposer")

LLMCallable = Union[Callable[[str], Any], Runnable]

import concurrent.futures
from .schemas import EnrichedIntent

class DecomposerNode:
    """
    Node responsible for decomposing a complex query into sub-queries.
    Now also handles Intent Enrichment and Complexity Classification.
    """
    def __init__(self, llm_map: dict[str, LLMCallable], registry: DatasourceRegistry, vector_store: Optional[OrchestratorVectorStore] = None):
        self.llm_map = llm_map
        self.registry = registry
        self.vector_store = vector_store
        
        self.prompt = ChatPromptTemplate.from_template(DECOMPOSER_PROMPT)
        self.chain = self.prompt | self.llm_map["decomposer"]
        
        self.enricher_prompt = ChatPromptTemplate.from_template(INTENT_ENRICHER_PROMPT)
        self.enricher_chain = self.enricher_prompt | self.llm_map["intent_enricher"]

        self.canonicalizer_prompt = ChatPromptTemplate.from_template(CANONICALIZATION_PROMPT)
        self.canonicalizer_chain = self.canonicalizer_prompt | self.llm_map["canonicalizer"]

    def _retrieve_context(self, query: str, enriched_terms: List[str] = []) -> str:
        """
        Retrieves relevant context (tables/schemas) using L1/L2 search strategies.
        Combines canonical query with enriched terms for better recall.
        
        Args:
            query: The canonical user query.
            enriched_terms: List of keywords/synonyms/entities from Intent Enricher.
            
        Returns:
            A formatted string for the prompt.
        """
        if not self.vector_store:
            return "No schema vector store available. Rely on general descriptions."
        
        search_query = query
        if enriched_terms:
            search_query += " " + " ".join(enriched_terms)
            
        try:
            results = self.vector_store.retrieve_routing_context(search_query, k=10)
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
            canonical_query = user_query
            enriched_intent = None
            
            def run_canonicalizer():
                return self.canonicalizer_chain.invoke({"user_query": user_query})
                
            def run_enricher():
                return self.enricher_chain.invoke({"user_query": user_query})

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future_canon = executor.submit(run_canonicalizer)
                future_enrich = executor.submit(run_enricher)
                
                try:
                    canonical_query = future_canon.result(timeout=10)
                except Exception as e:
                    logger.warning(f"Canonicalizer failed or timed out: {e}")
                
                try:
                    enriched_intent = future_enrich.result(timeout=10)
                except Exception as e:
                    logger.warning(f"Enricher failed or timed out: {e}")

            logger.info(f"Canonicalized: {canonical_query}")
            
            enriched_terms = []
            if enriched_intent:
                enriched_terms = enriched_intent.keywords + enriched_intent.entities + enriched_intent.synonyms
                logger.info(f"Enriched Terms: {enriched_terms}")

            if state.selected_datasource_id:
                logger.info(f"Direct execution requested for datasource: {state.selected_datasource_id}")
                
                candidate_tables = []
                if self.vector_store:
                    try:
                        search_q = canonical_query + " " + " ".join(enriched_terms)
                        candidate_tables = self.vector_store.retrieve_table_names(
                            search_q, 
                            datasource_id=[state.selected_datasource_id]
                        )
                    except Exception as e:
                        logger.warning(f"Direct vector search failed: {e}")
                 
                sq = SubQuery(
                    query=canonical_query,
                    datasource_id=state.selected_datasource_id,
                    candidate_tables=candidate_tables or [],
                    complexity=enriched_intent.complexity, 
                    reasoning=f"Direct execution for {state.selected_datasource_id}. Skipped LLM decomposition."
                )
                
                return {
                    "sub_queries": [sq],
                    "reasoning": [{"node": "decomposer", "content": sq.reasoning}]
                }

            context_str = self._retrieve_context(canonical_query, enriched_terms)
            
            profiles = self.registry.list_profiles()
            datasources_str = "\n".join([f"- {p.id}: {p.description}" for p in profiles])

            response: DecomposerResponse = self.chain.invoke({
                "user_query": user_query,
                "datasources": datasources_str,
                "schema_context": context_str
            })

            return {
                "sub_queries": response.sub_queries, 
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
                        error_code=ErrorCode.ORCHESTRATOR_CRASH,
                        stack_trace=str(e)
                    )
                ]
            }
