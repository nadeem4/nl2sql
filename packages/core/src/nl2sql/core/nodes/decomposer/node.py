from __future__ import annotations
from typing import Dict, Any, Callable, Union, TYPE_CHECKING, Optional
import json

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

if TYPE_CHECKING:
    from nl2sql.core.schemas import GraphState
    from nl2sql.core.vector_store import OrchestratorVectorStore

from .schemas import DecomposerResponse
from .prompts import DECOMPOSER_PROMPT
from nl2sql.core.datasource_registry import DatasourceRegistry
from nl2sql.core.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.core.logger import get_logger

logger = get_logger("decomposer")

LLMCallable = Union[Callable[[str], Any], Runnable]


class DecomposerNode:
    def __init__(
        self,
        llm: LLMCallable,
        registry: DatasourceRegistry,
        vector_store: Optional[OrchestratorVectorStore] = None,
    ):
        self.llm = llm
        self.registry = registry
        self.vector_store = vector_store
        self.prompt = ChatPromptTemplate.from_template(DECOMPOSER_PROMPT)
        self.chain = self.prompt | self.llm

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        node_name = "decomposer"

        try:
            if not state.entities:
                raise ValueError("Missing entities from Intent node")

            entity_matches: Dict[str, Any] = {}

            if self.vector_store:
                for entity in state.entities:
                    search_terms = " ".join(
                        [entity.name] + entity.required_attributes
                    )

                    results = self.vector_store.retrieve_routing_context(
                        search_terms, k=10
                    )

                    matches = []
                    for res in results:
                        ds = res.metadata.get("datasource_id")
                        if not ds:
                            continue
                        matches.append(
                            {
                                "datasource_id": ds,
                                "table": res.metadata.get("table_name"),
                                "type": res.metadata.get("type", "table"),
                            }
                        )

                    entity_matches[entity.entity_id] = matches

            response: DecomposerResponse = self.chain.invoke(
                {
                    "user_query": state.user_query,
                    "entities": json.dumps(
                        [e.model_dump() for e in state.entities]
                    ),
                    "entity_datasource_matches": json.dumps(entity_matches),
                }
            )

            return {
                "sub_queries": response.sub_queries,
                "entity_mapping": response.entity_mapping,
                "confidence": response.confidence,
                "reasoning": [
                    {"node": node_name, "content": response.reasoning}
                ],
            }

        except Exception as e:
            logger.error(f"Node {node_name} failed: {e}")

            return {
                "sub_queries": [],
                "entity_mapping": [],
                "confidence": 0.0,
                "reasoning": [
                    {
                        "node": node_name,
                        "content": f"Decomposition failed: {str(e)}",
                        "type": "error",
                    }
                ],
                "errors": [
                    PipelineError(
                        node=node_name,
                        message=f"Decomposition failed: {str(e)}",
                        severity=ErrorSeverity.CRITICAL,
                        error_code=ErrorCode.ORCHESTRATOR_CRASH,
                        stack_trace=str(e),
                    )
                ],
            }
