from __future__ import annotations
from typing import Dict, Any, TYPE_CHECKING

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from .schemas import DecomposerResponse, SubQuery, UnmappedSubQuery, PostCombineOp
from .prompts import DECOMPOSER_PROMPT
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext
import hashlib
import json

logger = get_logger("decomposer")



class DecomposerNode:
    """Orchestrates query decomposition and routing.

    Analyzes the user query to generate semantic sub-queries and combine groups.

    Attributes:
        llm (ChatOpenAI): The language model to use.
        prompt (ChatPromptTemplate): The prompt template.
        chain (Runnable): The execution chain.
    """

    def __init__(self, ctx: NL2SQLContext):
        """Initializes the DecomposerNode.

        Args:
            llm (LLMCallable): The LLM instance or runnable.
            vector_store (Optional[VectorStore]): Vector store for RAG.
        """
        self.node_name = self.__class__.__name__.lower().replace('node', '')
        self.llm = ctx.llm_registry.get_llm(self.node_name)
        self.prompt = ChatPromptTemplate.from_template(DECOMPOSER_PROMPT)
        self.chain = self.prompt | self.llm.with_structured_output(
            DecomposerResponse, method="function_calling"
        )

    def _stable_id(self, prefix: str, payload: Dict[str, Any]) -> str:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digest = hashlib.sha256(data.encode("utf-8")).hexdigest()[:12]
        return f"{prefix}_{digest}"

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """Executes the decomposer node.

        Invokes the LLM to produce semantic sub-queries and combine groups.

        Args:
            state (GraphState): The current execution state.

        Returns:
            Dict[str, Any]: Dictionary containing 'sub_queries', confidence, reasoning, etc.
        """
        try:
            resolver_response = state.datasource_resolver_response
            resolved_datasources = resolver_response.resolved_datasources if resolver_response else []
            resolved_payload = []
            resolved_ids = set()
            for entry in resolved_datasources:
                if hasattr(entry, "model_dump"):
                    data = entry.model_dump()
                elif isinstance(entry, dict):
                    data = entry
                else:
                    data = {"datasource_id": getattr(entry, "datasource_id", None)}
                resolved_payload.append(data)
                ds_id = data.get("datasource_id")
                if ds_id:
                    resolved_ids.add(ds_id)
            llm_response: DecomposerResponse = self.chain.invoke(
                {
                    "user_query": state.user_query,
                    "resolved_datasources": resolved_payload,
                }
            )

            final_sub_queries = []
            unmapped = list(llm_response.unmapped_subqueries or [])
            allowed_ids = set(resolver_response.allowed_datasource_ids or []) if resolver_response else set()
            unsupported_ids = set(resolver_response.unsupported_datasource_ids or []) if resolver_response else set()
            id_map: Dict[str, str] = {}

            for llm_sq in llm_response.sub_queries:
                datasource_id = (llm_sq.datasource_id or "").strip() or None
                if not datasource_id or datasource_id not in resolved_ids:
                    unmapped.append(
                        UnmappedSubQuery(
                            intent=llm_sq.intent,
                            reason="no_datasource",
                            datasource_id=datasource_id,
                            detail="Datasource is missing or not resolved for this sub-query.",
                        )
                    )
                    continue
                if datasource_id not in allowed_ids:
                    unmapped.append(
                        UnmappedSubQuery(
                            intent=llm_sq.intent,
                            reason="restricted_datasource",
                            datasource_id=datasource_id,
                            detail="Datasource is not allowed for the current user context.",
                        )
                    )
                    continue
                if datasource_id in unsupported_ids:
                    unmapped.append(
                        UnmappedSubQuery(
                            intent=llm_sq.intent,
                            reason="unsupported_datasource",
                            datasource_id=datasource_id,
                            detail="Datasource does not match any supported adapter capabilities.",
                        )
                    )
                    continue
                stable_id = self._stable_id(
                    "sq",
                    {
                        "datasource_id": datasource_id,
                        "intent": llm_sq.intent,
                        "metrics": [m.model_dump() for m in llm_sq.metrics],
                        "filters": [f.model_dump() for f in llm_sq.filters],
                        "group_by": [g.model_dump() for g in llm_sq.group_by],
                        "expected_schema": [c.model_dump() for c in llm_sq.expected_schema],
                    },
                )
                id_map[llm_sq.id] = stable_id
                sq = SubQuery(
                    id=stable_id,
                    intent=llm_sq.intent,
                    datasource_id=datasource_id,
                    metrics=llm_sq.metrics,
                    filters=llm_sq.filters,
                    group_by=llm_sq.group_by,
                    expected_schema=llm_sq.expected_schema,
                )
                final_sub_queries.append(sq)

            valid_ids = {sq.id for sq in final_sub_queries}
            combine_groups = []
            for group in llm_response.combine_groups:
                updated_inputs = []
                for inp in group.inputs:
                    mapped_id = id_map.get(inp.subquery_id, inp.subquery_id)
                    if mapped_id in valid_ids:
                        updated_inputs.append(inp.model_copy(update={"subquery_id": mapped_id}))
                if not updated_inputs:
                    continue
                combine_groups.append(group.model_copy(update={"inputs": updated_inputs}))

            combine_groups = sorted(combine_groups, key=lambda g: g.group_id)
            final_sub_queries = sorted(final_sub_queries, key=lambda s: s.id)

            post_combine_ops = []
            for op in llm_response.post_combine_ops or []:
                op_id = self._stable_id(
                    "op",
                    {
                        "target_group_id": op.target_group_id,
                        "operation": op.operation,
                        "filters": [f.model_dump() for f in op.filters],
                        "metrics": [m.model_dump() for m in op.metrics],
                        "group_by": [g.model_dump() for g in op.group_by],
                        "order_by": [o.model_dump() for o in op.order_by],
                        "limit": op.limit,
                        "expected_schema": [c.model_dump() for c in op.expected_schema],
                        "metadata": op.metadata,
                    },
                )
                post_combine_ops.append(op.model_copy(update={"op_id": op_id}))

            post_combine_ops = sorted(post_combine_ops, key=lambda o: o.op_id)

            response = DecomposerResponse(
                sub_queries=final_sub_queries,
                combine_groups=combine_groups,
                post_combine_ops=post_combine_ops,
                unmapped_subqueries=unmapped,
            )

            logger.info(f"Decomposer response: {response.model_dump_json(indent=2)}")

            return {
                "decomposer_response": response,
                "reasoning": [{"node": self.node_name, "content": "Decomposition completed."}],
            }

        except Exception as e:
            logger.error(f"Node {self.node_name} failed: {e}")

            return {
                "decomposer_response": DecomposerResponse(
                    sub_queries=[],
                    combine_groups=[],
                    post_combine_ops=[],
                    unmapped_subqueries=[],
                ),
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
