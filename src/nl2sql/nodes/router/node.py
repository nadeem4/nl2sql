from __future__ import annotations


from typing import Optional, TYPE_CHECKING, Dict, Any

if TYPE_CHECKING:
    from nl2sql.schemas import GraphState
from nl2sql.router_store import DatasourceRouterStore
from nl2sql.llm_registry import LLMRegistry
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.embeddings import EmbeddingService
from nl2sql.nodes.router.schemas import RoutingInfo, CandidateInfo
from nl2sql.settings import settings

from nl2sql.logger import get_logger

logger = get_logger("router")

class RouterNode:
    """
    Determines the best datasource for a given user query.
    
    Implements a multi-layer routing strategy:
    1. Layer 1: Vector Search (fast, low cost)
    2. Layer 2: Multi-Query Retrieval (robust, medium cost)
    3. Layer 3: LLM Reasoning (fallback, high cost)
    """

    def __init__(self, registry: LLMRegistry, datasource_registry: DatasourceRegistry, vector_store_path: str):
        """
        Initializes the RouterNode.

        Args:
            registry: The LLM registry for accessing agents.
            datasource_registry: The registry containing all available datasources.
            vector_store_path: Path to the vector store for routing.
        """
        self.registry = registry
        self.datasource_registry = datasource_registry
        self.vector_store_path = vector_store_path
        self._router_store: Optional[DatasourceRouterStore] = None

    def _get_store(self) -> DatasourceRouterStore:
        """Lazy loads the router store."""
        if not self._router_store:
            self._router_store = DatasourceRouterStore(persist_directory=self.vector_store_path)
        return self._router_store

    def __call__(self, state: GraphState, input_query: Optional[str] = None) -> Dict[str, Any]:
        """
        Executes the routing logic.

        Args:
            state: The current graph state.
            input_query: Optional query string to route (overrides state.user_query).

        Returns:
            Dictionary updates for the graph state.
        """
        node_name = "router"
        
        try:
            user_query = input_query if input_query else state.user_query
            

            if state.selected_datasource_id and not input_query:
                return {}

            print(f"--- Router Node: Routing query '{user_query}' ---")
            router_store = self._get_store()            
            target_id = None
            routing_layer = None
            reasoning = "" 
            
            l1_score = 0.0
            candidates = []
            
            canonical_llm = self.registry.router_canonicalizer_llm()
            canonical_query = router_store.canonicalize_query(user_query, canonical_llm)
            print(f"  -> Canonicalized: '{user_query}' => '{canonical_query}'")
            
            results = router_store.retrieve_with_score(canonical_query, k=5)
            
            candidates = [{"id": r[0], "score": r[1]} for r in results]
            
            if results:
                target_id, distance = results[0]
                l1_score = distance
                routing_layer = "layer_1"
                reasoning = f"Distance {distance:.3f} <= {settings.router_l1_threshold} threshold. (Canonical: {canonical_query})"
                
                if distance > settings.router_l1_threshold:
                    mq_llm = self.registry.router_multi_query_llm()
                    mq_results = router_store.multi_query_retrieve(user_query, mq_llm)
                    
                    if mq_results:
                        target_id = mq_results[0]
                        routing_layer = "layer_2"
                        reasoning = "Multi-query consensus selected this datasource."
                    else:
                        candidate_ids = {r[0] for r in results}
                        
                        all_profiles = self.datasource_registry.list_profiles()
                        profiles = [p for p in all_profiles if p.id in candidate_ids]
                        
                        if not profiles:
                            profiles = all_profiles
                        
                        
                        decision_llm = self.registry.router_decision_llm()
                        l3_id, l3_reasoning = router_store.llm_route(user_query, decision_llm, profiles)
                        
                        if l3_id:
                            target_id = l3_id
                            routing_layer = "layer_3"
                            reasoning = l3_reasoning
            
            if not target_id:
                raise ValueError(f"Could not route query '{user_query}' to any known datasource.")

            
            router_thoughts = []
            if reasoning:
                    router_thoughts.append(reasoning)
            
            output = {
                "datasource_id": {target_id},
                "selected_datasource_id": target_id,
                "routing_info": {
                    target_id: RoutingInfo(
                        layer=routing_layer,
                        l1_score=l1_score,
                        candidates=[CandidateInfo(id=c["id"], score=c["score"]) for c in candidates]
                    )
                },
                "thoughts": {"router": router_thoughts}
            }
            
            return output

        except Exception as e:
            return {
                "errors": [f"Routing error: {str(e)}"],
                "thoughts": {"router": [f"Error occurred: {str(e)}"]}
            }
