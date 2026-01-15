from __future__ import annotations
from typing import Dict, Any, Callable, Union, TYPE_CHECKING, Optional
import json

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from nl2sql.llm.registry import LLMRegistry

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState
    from nl2sql.services.vector_store import OrchestratorVectorStore
    from nl2sql.pipeline.nodes.semantic.node import SemanticAnalysisNode

from .schemas import DecomposerResponse, SubQuery
from .prompts import DECOMPOSER_PROMPT
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql_adapter_sdk import Table
from nl2sql.context import NL2SQLContext
import uuid

logger = get_logger("decomposer")



class DecomposerNode:
    """Orchestrates query decomposition and routing.

    Analyzes the user query and retrieved context to determine which datasource(s)
    should handle the request. Splits complex multi-source queries into sub-queries.

    Attributes:
        llm (ChatOpenAI): The language model to use.
        vector_store (Optional[OrchestratorVectorStore]): Store for retrieving context.
        prompt (ChatPromptTemplate): The prompt template.
        chain (Runnable): The execution chain.
    """

    def __init__(self, ctx: NL2SQLContext):
        """Initializes the DecomposerNode.

        Args:
            llm (LLMCallable): The LLM instance or runnable.
            vector_store (Optional[OrchestratorVectorStore]): Vector store for RAG.
        """
        self.node_name = self.__class__.__name__.lower().replace('node', '')
        self.llm = ctx.llm_registry.get_llm(self.node_name)
        self.rbac = ctx.rbac
        self.vector_store = ctx.vector_store
        self.prompt = ChatPromptTemplate.from_template(DECOMPOSER_PROMPT)
        self.chain = self.prompt | self.llm.with_structured_output(DecomposerResponse)

    def _get_relevant_docs(self, state: GraphState, override_query: str = None) -> list:
        """Helper to retrieve relevant docs from Vector Store based on user_context.

        Args:
            state (GraphState): Current execution state.
            override_query (str): Optional query string to use instead of state.user_query.

        Returns:
            list: A list of relevant documents.
        """

        if not self.vector_store:
            return []

        user_ctx = state.user_context
        allowed_ds = self.rbac.get_allowed_datasources(user_ctx)

        query_text = override_query or state.user_query

        if "*" in allowed_ds:
            return self.vector_store.retrieve_routing_context(
                query_text, k=20
            )
        elif allowed_ds:
            return self.vector_store.retrieve_routing_context(
                query_text, k=20, datasource_id=allowed_ds
            )
        else:
            logger.warning("AuthZ: No allowed_datasources found in user_context. Skipping retrieval.")
            return []

    def _check_user_access(self, state: GraphState) -> bool:
        """Checks if the user has access to any datasources.

        Args:
           state (GraphState): Current execution state.

        Returns:
            bool: True if access is allowed, False otherwise.
        """
        user_ctx = state.user_context
        allowed_ds = self.rbac.get_allowed_datasources(user_ctx)
        return bool(allowed_ds)

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """Executes the decomposer node.

        Retrieves context, invokes the LLM, and produces a routing decision.

        Args:
            state (GraphState): The current execution state.

        Returns:
            Dict[str, Any]: Dictionary containing 'sub_queries', confidence, reasoning, etc.
        """
        try:
            if not self._check_user_access(state):
                return {
                    "sub_queries": [],
                    "confidence": 0.0,
                    "reasoning": [
                        {"node": self.node_name, "content": "AuthZ: Request rejected. No allowed_datasources found in user_context."}
                    ],
                    "errors": [
                        PipelineError(
                            node=self.node_name,
                            message="Access Denied: User has no allowed datasources.",
                            severity=ErrorSeverity.CRITICAL,
                            error_code=ErrorCode.SECURITY_VIOLATION
                        )
                    ]
                }

            expanded_query = state.user_query

            if state.semantic_analysis:
                analysis = state.semantic_analysis
                if analysis.keywords or analysis.synonyms:
                    expanded_query = f"{analysis.canonical_query} {' '.join(analysis.keywords)} {' '.join(analysis.synonyms)}"
                    print(f"Expanded Query: {expanded_query}")

            retrieved_context = ""
            docs = self._get_relevant_docs(state, override_query=expanded_query)

            if not docs:
                logger.warning("No relevant docs found for query: %s", expanded_query)
                return {
                    "sub_queries": [],
                    "confidence": 0.0,
                    "reasoning": [
                        {"node": self.node_name, "content": f"Retrieval returned 0 documents for search query: '{expanded_query}'."}
                    ],
                    "errors": [
                        PipelineError(
                            node=self.node_name,
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
                    content_block = schema_json_str

                    if d_id not in ds_tables:
                        ds_tables[d_id] = []
                    ds_tables[d_id].append(Table.model_validate_json(schema_json_str))

                    context_lines.append(f"[Table] {name} (DS: {d_id}):\n{content_block}")
                elif d_type == "example":
                    context_lines.append(f"[Example] (DS: {d_id}): {doc.page_content}")

            retrieved_context = "\n\n".join(context_lines)

            llm_response: DecomposerResponse = self.chain.invoke(
                {
                    "user_query": state.user_query,
                    "retrieved_context": retrieved_context,
                }
            )

            final_sub_queries = []

            for llm_sq in llm_response.sub_queries:
                sq = SubQuery(
                    id=str(uuid.uuid4()),
                    query=llm_sq.query,
                    datasource_id=llm_sq.datasource_id,
                    complexity=llm_sq.complexity
                )

                if sq.datasource_id in ds_tables:
                    sq.relevant_tables = ds_tables[sq.datasource_id]

                final_sub_queries.append(sq)

            reasoning_list = []

            reasoning_list.append({"node": self.node_name, "content": llm_response.reasoning})

            return {
                "sub_queries": final_sub_queries,
                "confidence": llm_response.confidence,
                "output_mode": llm_response.output_mode,
                "reasoning": reasoning_list,
            }

        except Exception as e:
            logger.error(f"Node {self.node_name} failed: {e}")

            return {
                "sub_queries": [],
                "confidence": 0.0,
                "reasoning": [
                    {
                        "node": self.node_name,
                        "content": f"Decomposition failed: {str(e)}",
                        "type": "error",
                    }
                ],
                "errors": [
                    PipelineError(
                        node=self.node_name,
                        message=f"Decomposition failed: {str(e)}",
                        severity=ErrorSeverity.CRITICAL,
                        error_code=ErrorCode.ORCHESTRATOR_CRASH,
                        stack_trace=str(e),
                    )
                ],
            }
