from __future__ import annotations
from typing import Dict, Any, Callable, Union, TYPE_CHECKING, Optional
import json

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState
    from nl2sql.services.vector_store import OrchestratorVectorStore

from .schemas import DecomposerResponse
from .prompts import DECOMPOSER_PROMPT
from nl2sql.datasources import DatasourceRegistry
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger

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


    def _get_relevant_docs(self, state: GraphState) -> list:
        """
        Helper to retrieve relevant docs from Vector Store based on user_context.
        Returns a list of documents.
        """

        if not self.vector_store:
            return []

        user_ctx = state.user_context or {}
        allowed_ds = user_ctx.get("allowed_datasources") or []

        if "*" in allowed_ds:
            return self.vector_store.retrieve_routing_context(
                state.user_query, k=20
            )
        elif allowed_ds:
            return self.vector_store.retrieve_routing_context(
                state.user_query, k=20, datasource_id=allowed_ds
            )
        else:
            logger.warning("AuthZ: No allowed_datasources found in user_context. Skipping retrieval.")
            return []

    def _check_user_access(self, state: GraphState) -> bool:
        user_ctx = state.user_context or {}
        allowed_ds = user_ctx.get("allowed_datasources") or []
        return bool(allowed_ds)

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        node_name = "decomposer"

        try:
            if not self._check_user_access(state):
                return {
                    "sub_queries": [],
                    "confidence": 0.0,
                    "reasoning": [
                        {"node": node_name, "content": "AuthZ: Request rejected. No allowed_datasources found in user_context."}
                    ],
                    "errors": [
                    PipelineError(
                        node=node_name,
                        message="Access Denied: User has no allowed datasources.",
                        severity=ErrorSeverity.CRITICAL,
                        error_code=ErrorCode.SECURITY_VIOLATION
                    )
                ]
            }

            retrieved_context = ""            
            docs = self._get_relevant_docs(state)
            
            if not docs:
                logger.warning("No relevant docs found for query: %s", state.user_query)
                return {
                    "sub_queries": [],
                    "confidence": 0.0,
                    "reasoning": [
                        {"node": node_name, "content": "Retrieval returned 0 documents. Unable to generate SQL without schema context."}
                    ],
                    "errors": [
                        PipelineError(
                            node=node_name,
                            message="Data Not Found: No relevant tables found in allowed datasources.",
                            severity=ErrorSeverity.CRITICAL,
                            error_code=ErrorCode.SCHEMA_RETRIEVAL_FAILED
                        )
                    ]
                }
                
            context_lines = []
            ds_tables: Dict[str, list] = {}
            
            for doc in docs:
                d_type = doc.metadata.get("type", "table")
                d_id = doc.metadata.get("datasource_id", "unknown")
                name = doc.metadata.get("table_name", "unknown")
                
                if d_type == "table":
                    schema_json_str = doc.metadata.get("schema_json")
                    content_block = doc.page_content
                    
                    if schema_json_str:
                        try:
                            schema_obj = json.loads(schema_json_str)
                            content_block = json.dumps(schema_obj, indent=2)
                            
                            if d_id not in ds_tables:
                                ds_tables[d_id] = []
                            ds_tables[d_id].append(Table(**schema_obj))
                            
                        except Exception:
                            pass
                            
                    context_lines.append(f"[Table] {name} (DS: {d_id}):\n{content_block}")
                elif d_type == "example":
                        context_lines.append(f"[Example] (DS: {d_id}): {doc.page_content}")
            
            retrieved_context = "\n\n".join(context_lines)

            llm_response: DecomposerResponse = self.chain.invoke(
                {
                    "user_query": state.user_query,
                    "retrieved_context": retrieved_context or "No context available.",
                }
            )

            final_sub_queries = []
            
            for llm_sq in llm_response.sub_queries:
                sq = SubQuery(
                    query=llm_sq.query,
                    datasource_id=llm_sq.datasource_id,
                    complexity=llm_sq.complexity
                )

                if sq.datasource_id in ds_tables:
                    sq.relevant_tables = ds_tables[sq.datasource_id]
                
                final_sub_queries.append(sq)

            return {
                "sub_queries": final_sub_queries,
                "confidence": llm_response.confidence,
                "output_mode": llm_response.output_mode, 
                "reasoning": [
                    {"node": node_name, "content": llm_response.reasoning}
                ],
            }

        except Exception as e:
            logger.error(f"Node {node_name} failed: {e}")

            return {
                "sub_queries": [],
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
