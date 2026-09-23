from __future__ import annotations
from typing import Dict, Any, List, TYPE_CHECKING

from langchain_core.runnables import Runnable

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from .dag import build_execution_dag
from .schemas import DecomposerResponse, SubQuery, UnmappedSubQuery, PostCombineOp
from .prompts import DECOMPOSER_PROMPT
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext
from nl2sql.llm.wires import structured
import hashlib
import json

logger = get_logger("decomposer")



# Post-combine ops that are the SQL of a single sub-query: WHERE/HAVING,
# ORDER BY and LIMIT. An aggregate or project op re-shapes the rows, so a
# group with one of those is left to the aggregator.
_FOLDABLE_OPS = {"filter", "sort", "limit"}


def fold_single_input_ops(response: DecomposerResponse) -> DecomposerResponse:
    """Moves filter/sort/limit ops on a one-sub-query group into that sub-query.

    With one input there is nothing to combine, so the ops are the sub-query's
    own filters, ranking and top-N. Left as post-combine ops they reach only
    the aggregated answer, never the sub-query's SQL or rows. Every op on such
    a group targets the combine node directly (they are not chained), so
    folding them all is the only reading that applies all of them.
    """
    uses: Dict[str, int] = {}
    for group in response.combine_groups:
        for inp in group.inputs:
            uses[inp.subquery_id] = uses.get(inp.subquery_id, 0) + 1
    ops_by_group: Dict[str, List[PostCombineOp]] = {}
    for op in response.post_combine_ops:
        ops_by_group.setdefault(op.target_group_id, []).append(op)

    sub_queries = {sq.id: sq for sq in response.sub_queries}
    folded_groups = set()
    for group in response.combine_groups:
        ops = ops_by_group.get(group.group_id, [])
        if not ops or len(group.inputs) != 1 or any(op.operation not in _FOLDABLE_OPS for op in ops):
            continue
        sq = sub_queries.get(group.inputs[0].subquery_id)
        if sq is None or uses.get(sq.id) != 1:
            continue
        filters, order_by, limit = list(sq.filters), list(sq.order_by), sq.limit
        for op in ops:
            filters += [f for f in op.filters if f not in filters]
            order_by += [o for o in op.order_by if o not in order_by]
            if op.limit is not None:
                limit = op.limit if limit is None else min(limit, op.limit)
        sub_queries[sq.id] = sq.model_copy(update={"filters": filters, "order_by": order_by, "limit": limit})
        folded_groups.add(group.group_id)

    if not folded_groups:
        return response
    return response.model_copy(update={
        "sub_queries": [sub_queries[sq.id] for sq in response.sub_queries],
        "post_combine_ops": [op for op in response.post_combine_ops if op.target_group_id not in folded_groups],
    })


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
        self.prompt = DECOMPOSER_PROMPT
        self.chain = self.prompt | structured(self.llm, DecomposerResponse)

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
            if not resolver_response or not resolver_response.resolved_datasources:
                raise ValueError("Unable to resolve any datasource for the current user query.")
            
            resolved_datasources = resolver_response.resolved_datasources 
            resolved_payload = []
            resolved_ids = set()
            schema_version_map = {}
            for datasource in resolved_datasources:
                resolved_payload.append(datasource.model_dump())
                resolved_ids.add(datasource.datasource_id)
                schema_version_map[datasource.datasource_id] = datasource.schema_version

            llm_response: DecomposerResponse = self.chain.invoke(
                {
                    "user_query": state.user_query,
                    # Sorted keys: nested metadata prints in a fixed order.
                    "resolved_datasources": json.dumps(
                        resolved_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
                    ),
                }
            )

            llm_response = fold_single_input_ops(llm_response)

            final_sub_queries = []
            unmapped = []
            allowed_ids = set(resolver_response.allowed_datasource_ids)
            unsupported_ids = set(resolver_response.unsupported_datasource_ids)
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
                        "order_by": [o.model_dump() for o in llm_sq.order_by],
                        "limit": llm_sq.limit,
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
                    order_by=llm_sq.order_by,
                    limit=llm_sq.limit,
                    expected_schema=llm_sq.expected_schema,
                    schema_version=schema_version_map.get(datasource_id),
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

            # The execution DAG is a pure function of this response, so it is
            # built here rather than in a node of its own. It has its own
            # failure: a decomposition can be well-formed and still not make a
            # runnable graph (two identical sub-queries share one stable id, so
            # the graph they describe has a node with two of everything). The
            # response is returned either way -- the layer router ends a run
            # with no DAG, leaving this error as its cause.
            try:
                execution_dag = build_execution_dag(response)
            except Exception as exc:
                logger.error(f"Execution DAG generation failed: {exc}")
                return {
                    "decomposer_response": response,
                    "reasoning": [{"node": self.node_name,
                                   "content": f"Execution DAG generation failed: {exc}",
                                   "type": "error"}],
                    "errors": [
                        PipelineError(
                            node=self.node_name,
                            message=f"Execution DAG generation failed: {exc}",
                            severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.PLANNER_FAILED,
                        )
                    ],
                }

            return {
                "decomposer_response": response,
                "execution_dag": execution_dag,
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
