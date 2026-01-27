from __future__ import annotations
from typing import List, Dict, Any, TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.pipeline.nodes.global_planner.schemas import ExecutionDAG, LogicalNode, LogicalEdge
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql_adapter_sdk.contracts import ResultFrame, ResultColumn

from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext

logger = get_logger("aggregator")


class EngineAggregatorNode:
    """Node responsible for deterministic aggregation using ExecutionDAG."""

    def __init__(self, ctx: NL2SQLContext):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.ctx = ctx



    def _normalize_result_frame(self, frame: ResultFrame) -> ResultFrame:
        if frame.columns:
            return frame
        # fallback: infer columns from row dicts if needed (legacy)
        rows = frame.to_row_dicts()
        if not rows:
            return frame
        columns = list(rows[0].keys())
        col_specs = [ResultColumn(name=c, type="unknown") for c in columns]
        values = [[row.get(c) for c in columns] for row in rows]
        return ResultFrame(columns=col_specs, rows=values, row_count=len(values), success=frame.success)

    def _execute_execution_dag(self, state: GraphState) -> Dict[str, ResultFrame]:
        dag: ExecutionDAG = getattr(state.global_planner_response, "execution_dag", None)
        if not dag:
            raise ValueError("No ExecutionDAG found for aggregation.")

        results = state.results or {}
        node_index: Dict[str, LogicalNode] = {n.node_id: n for n in dag.nodes}
        incoming_edges: Dict[str, List[LogicalEdge]] = {}
        for edge in dag.edges:
            incoming_edges.setdefault(edge.to_id, []).append(edge)

        def get_frame(node_id: str) -> ResultFrame:
            if node_id in results:
                frame = self.ctx.result_store.get(results[node_id])
                return self._normalize_result_frame(frame)
            raise PipelineError(
                node=self.node_name,
                message=f"Missing execution result for node {node_id}.",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.EXECUTION_ERROR,
            )

        execution_order = self._topological_order(dag.nodes)
        computed: Dict[str, ResultFrame] = {}
        computed_refs: Dict[str, str] = {}

        for node_id in execution_order:
            node = node_index[node_id]
            if node.kind == "scan":
                computed[node_id] = get_frame(node_id)
                computed_refs[node_id] = results.get(node_id)
                continue

            upstream = incoming_edges.get(node_id, [])
            input_frames = [(edge, computed[edge.from_id]) for edge in upstream]

            if node.kind == "combine":
                computed[node_id] = self._execute_combine(node, input_frames)
                continue

            if node.kind.startswith("post_"):
                if len(input_frames) != 1:
                    raise PipelineError(
                        node=self.node_name,
                        message=f"Post-combine node '{node_id}' expects a single input.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.AGGREGATOR_FAILED,
                    )
                computed[node_id] = self._execute_post_op(node, input_frames[0][1])
                continue

            raise PipelineError(
                node=self.node_name,
                message=f"Unsupported logical node kind '{node.kind}'.",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.AGGREGATOR_FAILED,
            )

        for node_id, frame in computed.items():
            if node_id not in computed_refs:
                result_id = self.ctx.result_store.put(
                    frame,
                    metadata={
                        "node_id": node_id,
                        "dag_id": getattr(dag, "dag_id", None),
                        "trace_id": getattr(state, "trace_id", None),
                    },
                )
                computed_refs[node_id] = result_id

        terminal_nodes = self._terminal_node_ids(dag.nodes, dag.edges)
        if not terminal_nodes:
            raise PipelineError(
                node=self.node_name,
                message="No terminal nodes found in DAG.",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.AGGREGATOR_FAILED,
            )
        return {node_id: computed[node_id] for node_id in terminal_nodes if node_id in computed}, computed_refs

    def _terminal_node_ids(self, nodes: List[LogicalNode], edges: List[LogicalEdge]) -> List[str]:
        node_ids = {n.node_id for n in nodes}
        outgoing = {edge.from_id for edge in edges}
        terminals = sorted([node_id for node_id in node_ids if node_id not in outgoing])
        return terminals

    def _topological_order(self, nodes: List[LogicalNode]) -> List[str]:
        node_map = {n.node_id: n for n in nodes}
        indegree: Dict[str, int] = {n.node_id: 0 for n in nodes}
        dependents: Dict[str, List[str]] = {n.node_id: [] for n in nodes}
        for node in nodes:
            for parent in node.inputs:
                if parent not in node_map:
                    continue
                indegree[node.node_id] += 1
                dependents[parent].append(node.node_id)
        queue = sorted([n_id for n_id, deg in indegree.items() if deg == 0])
        order = []
        while queue:
            current = queue.pop(0)
            order.append(current)
            for child in sorted(dependents[current]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if len(order) != len(nodes):
            raise PipelineError(
                node=self.node_name,
                message="ExecutionDAG contains a cycle.",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.AGGREGATOR_FAILED,
            )
        return order

    def _execute_combine(
        self,
        node: LogicalNode,
        input_frames: List[Tuple[LogicalEdge, ResultFrame]],
    ) -> ResultFrame:
        operation = node.attributes.get("operation")
        join_keys = node.attributes.get("join_keys", [])
        if not input_frames:
            return ResultFrame()

        ordered_frames = self._order_inputs(input_frames)
        frames = [self._normalize_result_frame(f) for _, f in ordered_frames]

        if operation == "standalone":
            return frames[0]
        if operation == "union":
            return self._union_frames(frames)
        if operation == "join":
            return self._join_frames(frames, join_keys)
        if operation == "compare":
            return self._compare_frames(frames, join_keys)

        raise PipelineError(
            node=self.node_name,
            message=f"Combine operation '{operation}' not supported.",
            severity=ErrorSeverity.ERROR,
            error_code=ErrorCode.AGGREGATOR_FAILED,
        )

    def _order_inputs(
        self,
        inputs: List[Tuple[LogicalEdge, ResultFrame]],
    ) -> List[Tuple[LogicalEdge, ResultFrame]]:
        def role_rank(role: Optional[str]) -> int:
            order = {"left": 0, "base": 0, "primary": 0, "right": 1, "compare": 1, "secondary": 1}
            return order.get(role or "", 2)

        return sorted(inputs, key=lambda pair: (role_rank(pair[0].role), pair[0].from_id))

    def _union_frames(self, frames: List[ResultFrame]) -> ResultFrame:
        if not frames:
            return ResultFrame()
        base = frames[0]
        base_cols = [c.name for c in base.columns]
        for f in frames[1:]:
            if [c.name for c in f.columns] != base_cols:
                raise PipelineError(
                    node=self.node_name,
                    message="Union requires matching column names.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.AGGREGATOR_FAILED,
                )
        rows = []
        for f in frames:
            rows.extend(f.rows)
        return ResultFrame(
            success=True,
            columns=base.columns,
            rows=rows,
            row_count=len(rows),
        )

    def _join_frames(self, frames: List[ResultFrame], join_keys: List[Dict[str, Any]]) -> ResultFrame:
        if len(frames) < 2:
            return frames[0]
        left = frames[0].to_row_dicts()
        right = frames[1].to_row_dicts()
        left_cols = [c.name for c in frames[0].columns]
        right_cols = [c.name for c in frames[1].columns]
        if not join_keys:
            raise PipelineError(
                node=self.node_name,
                message="Join requires at least one shared column.",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.AGGREGATOR_FAILED,
            )

        right_index: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
        for row in right:
            key = tuple(row.get(k.get("right")) for k in join_keys)
            right_index.setdefault(key, []).append(row)

        output_rows = []
        for lrow in left:
            key = tuple(lrow.get(k.get("left")) for k in join_keys)
            for rrow in right_index.get(key, []):
                merged = {k.get("left"): lrow.get(k.get("left")) for k in join_keys}
                for col in left_cols:
                    if any(col == k.get("left") for k in join_keys):
                        continue
                    merged[col] = lrow.get(col)
                for col in right_cols:
                    if any(col == k.get("right") for k in join_keys):
                        continue
                    if col in merged:
                        merged[f"right_{col}"] = rrow.get(col)
                    else:
                        merged[col] = rrow.get(col)
                output_rows.append(merged)

        return ResultFrame.from_row_dicts(output_rows)

    def _compare_frames(self, frames: List[ResultFrame], join_keys: List[Dict[str, Any]]) -> ResultFrame:
        if len(frames) < 2:
            return frames[0]
        left = frames[0].to_row_dicts()
        right = frames[1].to_row_dicts()
        left_cols = [c.name for c in frames[0].columns]
        right_cols = [c.name for c in frames[1].columns]
        if not join_keys:
            raise PipelineError(
                node=self.node_name,
                message="Compare requires at least one shared column.",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.AGGREGATOR_FAILED,
            )

        right_index: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        for row in right:
            key = tuple(row.get(k.get("right")) for k in join_keys)
            right_index[key] = row

        output_rows = []
        for lrow in left:
            key = tuple(lrow.get(k.get("left")) for k in join_keys)
            rrow = right_index.get(key)
            if not rrow:
                continue
            diff = False
            merged = {k.get("left"): lrow.get(k.get("left")) for k in join_keys}
            for col in left_cols:
                if any(col == k.get("left") for k in join_keys):
                    continue
                lval = lrow.get(col)
                rval = rrow.get(col)
                merged[f"left_{col}"] = lval
                merged[f"right_{col}"] = rval
                if lval != rval:
                    diff = True
            if diff:
                output_rows.append(merged)

        return ResultFrame.from_row_dicts(output_rows)

    def _execute_post_op(self, node: LogicalNode, frame: ResultFrame) -> ResultFrame:
        rows = frame.to_row_dicts()
        operation = node.attributes.get("operation")
        if operation == "filter":
            rows = self._apply_filters(rows, node.attributes.get("filters", []))
        elif operation == "aggregate":
            rows = self._apply_aggregate(
                rows,
                node.attributes.get("group_by", []),
                node.attributes.get("metrics", []),
            )
        elif operation == "project":
            rows = self._apply_project(rows, node.attributes.get("expected_schema", []))
        elif operation == "sort":
            rows = self._apply_sort(rows, node.attributes.get("order_by", []))
        elif operation == "limit":
            rows = self._apply_limit(rows, node.attributes.get("limit"))
        else:
            raise PipelineError(
                node=self.node_name,
                message=f"Unsupported post-combine operation '{operation}'.",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.AGGREGATOR_FAILED,
            )
        return ResultFrame.from_row_dicts(rows)

    def _apply_filters(self, rows: List[Dict[str, Any]], filters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def check(row: Dict[str, Any], flt: Dict[str, Any]) -> bool:
            attr = flt.get("attribute")
            op = flt.get("operator")
            val = flt.get("value")
            current = row.get(attr)
            if op == "=":
                return current == val
            if op == "!=":
                return current != val
            if op == ">":
                return current is not None and current > val
            if op == ">=":
                return current is not None and current >= val
            if op == "<":
                return current is not None and current < val
            if op == "<=":
                return current is not None and current <= val
            if op == "between" and isinstance(val, list) and len(val) == 2:
                return current is not None and val[0] <= current <= val[1]
            if op == "in" and isinstance(val, list):
                return current in val
            if op == "contains" and isinstance(current, str):
                return str(val) in current
            return False

        for flt in filters:
            rows = [row for row in rows if check(row, flt)]
        return rows

    def _apply_aggregate(
        self,
        rows: List[Dict[str, Any]],
        group_by: List[Dict[str, Any]],
        metrics: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        group_keys = [g.get("attribute") for g in group_by]
        buckets: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
        for row in rows:
            key = tuple(row.get(k) for k in group_keys)
            buckets.setdefault(key, []).append(row)

        output = []
        for key, bucket in buckets.items():
            out_row = {k: v for k, v in zip(group_keys, key)}
            for metric in metrics:
                name = metric.get("name")
                agg = metric.get("aggregation")
                values = [r.get(name) for r in bucket if r.get(name) is not None]
                if agg == "count":
                    out_row[name] = len(bucket)
                elif agg == "sum":
                    out_row[name] = sum(values) if values else 0
                elif agg == "avg":
                    out_row[name] = (sum(values) / len(values)) if values else 0
                elif agg == "min":
                    out_row[name] = min(values) if values else None
                elif agg == "max":
                    out_row[name] = max(values) if values else None
                else:
                    out_row[name] = None
            output.append(out_row)
        return output

    def _apply_project(self, rows: List[Dict[str, Any]], expected_schema: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        columns = [c.get("name") for c in expected_schema if c.get("name")]
        if not columns:
            return rows
        return [{k: row.get(k) for k in columns} for row in rows]

    def _apply_sort(self, rows: List[Dict[str, Any]], order_by: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        for spec in reversed(order_by or []):
            attr = spec.get("attribute")
            reverse = spec.get("direction", "asc") == "desc"
            rows = sorted(rows, key=lambda r: r.get(attr), reverse=reverse)
        return rows

    def _apply_limit(self, rows: List[Dict[str, Any]], limit: Optional[int]) -> List[Dict[str, Any]]:
        if limit is None:
            return rows
        return rows[: max(0, int(limit))]


    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """Executes the aggregation logic."""
        try:
            terminal_frames, computed_refs = self._execute_execution_dag(state)
            aggregator_response = AggregatorResponse(
                terminal_results={
                    node_id: frame.to_row_dicts() for node_id, frame in terminal_frames.items()
                },
                computed_refs=computed_refs,
            )
            return {
                "aggregator_response": aggregator_response,
                "results": computed_refs,
                "reasoning": [{"node": self.node_name, "content": "ExecutionDAG aggregation executed successfully."}]
            }

        except Exception as e:
            logger.error(f"Node {self.node_name} failed: {e}")
            return {
                "aggregator_response": AggregatorResponse(),
                "reasoning": [{"node": self.node_name, "content": f"Error: {str(e)}", "type": "error"}],
                "errors": [
                    PipelineError(
                        node=self.node_name,
                        message=f"Aggregator failed: {str(e)}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.AGGREGATOR_FAILED
                    )
                ]
            }
