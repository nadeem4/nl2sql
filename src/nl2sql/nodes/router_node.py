from __future__ import annotations

import time
import tiktoken
from typing import Optional

from nl2sql.schemas import GraphState
from nl2sql.router_store import DatasourceRouterStore
from nl2sql.llm_registry import LLMRegistry
from nl2sql.datasource_registry import DatasourceRegistry


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

    def __call__(self, state: GraphState) -> GraphState:
        """
        Executes the routing logic.

        Args:
            state: The current graph state.

        Returns:
            The updated graph state with the selected `datasource_id`.
        """
        # If datasource_id is already set (e.g. forced via CLI), skip routing
        if state.datasource_id:
            return state

        query = state.user_query
        router_store = self._get_store()
        
        # Metrics
        start_time = time.perf_counter()
        try:
            enc = tiktoken.encoding_for_model("text-embedding-3-small")
            tokens = len(enc.encode(query))
        except:
            tokens = 0

        target_id = "manufacturing_sqlite" # Default fallback
        
        try:
            # Layer 1 & 2: Retrieve with Score
            results = router_store.retrieve_with_score(query)
            
            if results:
                target_id, distance = results[0]
                
                # Confidence Gate (Layer 2)
                if distance > 0.4:
                    llm = self.registry._base_llm("planner") 
                    mq_results = router_store.multi_query_retrieve(query, llm)
                    
                    if mq_results:
                        target_id = mq_results[0]
                    else:
                        # Layer 3: LLM Fallback
                        profiles = self.datasource_registry.list_profiles()
                        l3_result = router_store.llm_route(query, llm, profiles)
                        if l3_result:
                            target_id = l3_result
        except Exception as e:
            # Log error but fallback to default
            state.errors.append(f"Routing error: {str(e)}")

        duration = time.perf_counter() - start_time
        
        # Update state
        state.datasource_id = target_id
        state.latency["router"] = duration
        
        # We don't have a structured place for router tokens in GraphState yet,
        # but we could add it to a generic metrics dict if needed.
        
        return state
