from __future__ import annotations

import time
import tiktoken
from typing import Optional

from nl2sql.schemas import GraphState
from nl2sql.router_store import DatasourceRouterStore
from nl2sql.llm_registry import LLMRegistry
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.embeddings import EmbeddingService


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

    def __call__(self, state: GraphState, input_query: Optional[str] = None) -> GraphState:
        """
        Executes the routing logic.

        Args:
            state: The current graph state.
            input_query: Optional query string to route (overrides state.user_query).

        Returns:
            The updated graph state with the selected `datasource_id`.
        """
        user_query = input_query if input_query else state.user_query
        

        if state.datasource_id and not input_query:
            return state

        print(f"--- Router Node: Routing query '{user_query}' ---")
        router_store = self._get_store()
        
        # Metrics
        start_time = time.perf_counter()
        try:
            model_name = EmbeddingService.get_model_name()
            enc = tiktoken.encoding_for_model(model_name)
            tokens = len(enc.encode(user_query))
        except:
            tokens = 0

        target_id = "manufacturing_sqlite" # Default fallback
        
        try:
            # Layer 1 & 2: Retrieve with Score
            results = router_store.retrieve_with_score(user_query)
            
            if results:
                target_id, distance = results[0]
                
                # Confidence Gate (Layer 2)
                if distance > 0.4:
                    llm = self.registry._base_llm("planner") 
                    mq_results = router_store.multi_query_retrieve(user_query, llm)
                    
                    if mq_results:
                        target_id = mq_results[0]
                    else:
                        # Layer 3: LLM Fallback
                        profiles = self.datasource_registry.list_profiles()
                        l3_result = router_store.llm_route(user_query, llm, profiles)
                        if l3_result:
                            target_id = l3_result
        except Exception as e:
            # Log error but fallback to default
            state.errors.append(f"Routing error: {str(e)}")

        duration = time.perf_counter() - start_time
        
        # Update state
        state.datasource_id = target_id
        state.latency["router"] = duration
        
        if "router" not in state.thoughts:
            state.thoughts["router"] = []
        state.thoughts["router"].append(f"Selected Datasource: {target_id}")
        state.thoughts["router"].append(f"Latency: {duration:.4f}s")
        
        return state
