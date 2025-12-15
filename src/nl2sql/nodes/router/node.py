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
        """
        node_name = "router"
        
        try:
            user_query = input_query if input_query else state.user_query
            
            if state.selected_datasource_id and not input_query:
                return {}

            print(f"--- Router Node: Routing query '{user_query}' ---")
            
            router_reasoning = []
            target_id = None
            routing_layer = None
            l1_score = 0.0
            candidates = []
            router_store = self._get_store()
            
            canonical_llm = self.registry.router_canonicalizer_llm()
            canonical_query = router_store.canonicalize_query(user_query, canonical_llm)
            print(f"  -> Canonicalized: '{user_query}' => '{canonical_query}'")
            
            l1_result = self._run_l1(router_store, user_query, canonical_query)
            target_id = l1_result.get("target_id")
            routing_layer = l1_result.get("layer")
            l1_score = l1_result.get("score", 0.0)
            candidates = l1_result.get("candidates", [])
            
            if l1_result.get("reasoning"):
                router_reasoning.append(l1_result['reasoning'])
            
            if not target_id:
                l2_result = self._run_l2(router_store, user_query)
                target_id = l2_result.get("target_id")
                
                if l2_result.get("reasoning"):
                    router_reasoning.append(l2_result['reasoning'])
                
                if target_id:
                    routing_layer = l2_result.get("layer")

            if not target_id:
                l3_result = self._run_l3(router_store, user_query, candidates)
                target_id = l3_result.get("target_id")
                
                if l3_result.get("reasoning"):
                    router_reasoning.append(l3_result['reasoning'])
                     
                if target_id:
                    routing_layer = l3_result.get("layer")

            if not target_id:
                raise ValueError(f"Could not route query '{user_query}' to any known datasource.")

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
                "reasoning": [{"node": "router", "content": r} for r in router_reasoning]
            }
            
            return output

        except Exception as e:
            return {
                "errors": [f"Routing error: {str(e)}"],
                "reasoning": [{"node": "router", "content": f"Error occurred: {str(e)}", "type": "error"}]
            }

    def _run_l1(self, store: DatasourceRouterStore, query: str, canonical_query: str) -> Dict[str, Any]:
        """Executes Layer 1: Vector Search."""
        results = store.retrieve_with_score(canonical_query, k=5)
        reasoning = "Layer 1: Vector Search\n"
        res = {
            "candidates": [],
            "layer": "layer_1",
            "score": 0.0,
            "target_id": None,
            "reasoning": reasoning
        }
        
        if not results:
            reasoning += "\t ->No results found."
            res["reasoning"] = reasoning
            return res

        candidates = [{"id": r[0], "score": r[1]} for r in results]
        res["candidates"] = candidates

        target_id, distance = results[0]
        res["score"] = distance
        
        if distance <= settings.router_l1_threshold:
            res["reasoning"] += f"\t ->Distance {distance:.3f} <= {settings.router_l1_threshold} threshold. (Canonical: {canonical_query})"
            res["target_id"] = target_id
        else:
            res["reasoning"] += "\t ->Threshold exceeded, falling through"
                 
        return res

    def _run_l2(self, store: DatasourceRouterStore, query: str) -> Dict[str, Any]:
        """Executes Layer 2: Multi-Query Retrieval."""
        mq_llm = self.registry.router_multi_query_llm()
        mq_results, metadata = store.multi_query_retrieve(query, mq_llm)
        
        reasoning = "Layer 2: Multi-Query Retrieval\n"
        variations = metadata.get("variations", [])
        if variations:
            reasoning += f"\t -> Generated {len(variations)} variations: {variations}\n"
        
        if mq_results:
            return {
                "target_id": mq_results[0],
                "layer": "layer_2",
                "reasoning": reasoning + f"\t -> Multi-query consensus selected: {mq_results[0]} (Votes: {metadata.get('votes', {})})"
            }
        
        return {
            "target_id": None,
            "layer": "layer_2",
            "reasoning": reasoning + "\t -> Multi-query consensus failed."
        }

    def _run_l3(self, store: DatasourceRouterStore, query: str, candidates: List[Dict]) -> Dict[str, Any]:
        """Executes Layer 3: LLM Reasoning."""
        reasoning = "Layer 3: LLM Decision\n"
        
        candidate_ids = {c["id"] for c in candidates}
        all_profiles = self.datasource_registry.list_profiles()
        profiles = [p for p in all_profiles if p.id in candidate_ids]
        
        if not profiles:
            profiles = all_profiles

        decision_llm = self.registry.router_decision_llm()
        l3_id, l3_reasoning = store.llm_route(query, decision_llm, profiles)
        
        return {
            "target_id": l3_id,
            "layer": "layer_3",
            "reasoning": reasoning + f"\t ->{l3_reasoning}"
        }
