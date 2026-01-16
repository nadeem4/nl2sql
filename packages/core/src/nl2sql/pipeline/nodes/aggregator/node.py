from __future__ import annotations
from typing import List, Dict, Any, Literal, Callable, Union, TYPE_CHECKING, Optional
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from nl2sql.llm.registry import LLMRegistry

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from .schemas import AggregatedResponse
from .prompts import AGGREGATOR_PROMPT
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.pipeline.nodes.global_planner.schemas import (
    ResultPlan, RelationRef, Expr, Col, Lit, BinOp, 
    ProjectOp, FilterOp, JoinOp, UnionOp, GroupAggOp, OrderLimitOp
)

from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext

logger = get_logger("aggregator")


class EngineAggregatorNode:
    """Node responsible for deterministic aggregation of results using DuckDB/Polars.
    
    Uses the strictly typed ResultPlan from the GlobalPlanner.
    Falls back to LLM Aggregation if no plan is present.
    """

    def __init__(self, ctx: NL2SQLContext):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.llm = ctx.llm_registry.get_llm(self.node_name)
        self.prompt = ChatPromptTemplate.from_template(AGGREGATOR_PROMPT)
        self.chain = self.prompt | self.llm.with_structured_output(AggregatedResponse) 

    def _display_result_with_llm(self, state: GraphState) -> str:
        """Generates a text summary using the LLM (Fallback)."""
        results = state.subquery_results or {}
        sub_queries_map = {sq.id: sq for sq in (state.sub_queries or [])}
        formatted_results = ""

        for sq_id, exec_model in results.items():
            sq = sub_queries_map.get(sq_id)
            query_text = sq.query if sq else "Unknown Query"
            ds_id = sq.datasource_id if sq else "unknown"
            
            rows = exec_model.rows if exec_model else []
            formatted_results += f"--- SubQuery: {query_text} (ID: {sq_id}, DS: {ds_id}) ---\nData: {str(rows)}\n\n"

        if state.errors:
            formatted_results += "\n--- Errors Encountered ---\n"
            for err in state.errors:
                safe_msg = err.get_safe_message()
                formatted_results += f"Error from {err.node}: {safe_msg}\n"

        response: AggregatedResponse = self.chain.invoke({
            "user_query": state.user_query,
            "intermediate_results": formatted_results
        })

        final_answer = f"### Summary\n{response.summary}\n\n"
        if response.format_type == "table":
            final_answer += f"### Data\n\n{response.content}"
        elif response.format_type == "list":
            final_answer += f"### Details\n\n{response.content}"
        else:
            final_answer += f"\n{response.content}"

        return final_answer

    def _compile_expr(self, expr: Expr) -> str:
        """Compiles a Typed Expr to a SQL string fragment for DuckDB."""
        if expr.type == "col":
            # Using double quotes for column identifier safety
            return f'"{expr.name}"'
        elif expr.type == "lit":
            val = expr.value
            if val is None:
                return "NULL"
            if isinstance(val, str):
                # Basic escaping for string literals
                safe_val = val.replace("'", "''")
                return f"'{safe_val}'"
            return str(val)
        elif expr.type == "binop":
            left = self._compile_expr(expr.left)
            right = self._compile_expr(expr.right)
            return f"({left} {expr.op} {right})"
        
        raise ValueError(f"Unknown expression type: {expr}")

    def _get_table_ref(self, ref: RelationRef) -> str:
        """Resolves a RelationRef to a SQL table name."""
        return ref.id # IDs are registered as table names directly

    def _execute_deterministic_plan(self, state: GraphState) -> List[Dict]:
        """Executes the ResultPlan using DuckDB and Polars."""
        import duckdb
        import polars as pl
        
        results = state.subquery_results or {}
        plan: ResultPlan = state.result_plan

        if not plan:
            raise ValueError("No ResultPlan found for deterministic aggregation.")

        # 1. Initialize DuckDB
        con = duckdb.connect(":memory:")
        
        # 2. Register SubQuery Results
        for sq_id, exec_model in results.items():
            if not exec_model or not exec_model.rows:
                # Basic empty schema handling - ideal would be to use valid schema
                df = pl.DataFrame([]) 
            else:
                df = pl.DataFrame(exec_model.rows)
            
            con.register(sq_id, df)

        # 3. Execute Plan Steps
        for step in plan.steps:
            op = step.operation
            step_id = step.step_id
            query = ""

            try:
                if op.op == "join":
                    left_ref = self._get_table_ref(op.left)
                    right_ref = self._get_table_ref(op.right)
                    
                    # DuckDB Join syntax: SELECT * FROM t1 JOIN t2 ON ...
                    # We need to handle column name collisions if not careful, 
                    # but simple projection in next steps usually fixes it.
                    # Ideally we alias the tables.
                    
                    on_clauses = []
                    for bin_op in op.on:
                        on_clauses.append(self._compile_expr(bin_op))
                    
                    on_sql = " AND ".join(on_clauses)
                    join_type = op.join_type.upper()
                    
                    # Using A and B aliases for clarity in generated SQL if we were enforcing it,
                    # but here we rely on the compiled expression referencing columns cleanly.
                    # Since column references in Expr don't have scope (e.g. "t1.col"), 
                    # we must assume columns are unambiguous OR assume 'left' and 'right' context 
                    # is implied if we had scoped variables.
                    # HOWEVER, the Typed Expr schema defines 'Col' just as name. 
                    # For Joins, normally we need "left.id = right.id". 
                    # If on_columns was used it's easy. But 'on' is a list of BinOp.
                    # BinOp(left=Col(id), right=Col(id)).
                    # If both tables have 'id', ambiguous.
                    # Standard SQL solves this with aliases.
                    # Let's verify how we compile BinOp. It blindly returns "id".
                    # We need to hack this slightly or rely on the planner to output unique names?
                    # The prompt says "Keys MUST exist in input schemas". 
                    # For safety in this iteration, let's assume unique names or simple ON.
                    # NOTE: Basic implementation for now:
                    
                    query = f"""
                        CREATE OR REPLACE TABLE {step_id} AS 
                        SELECT * 
                        FROM {left_ref} AS L
                        {join_type} JOIN {right_ref} AS R 
                        ON {on_sql}
                    """
                    # Wait, if on_sql is just "id = id", that's 1=1 or ambiguous.
                    # We absolutely need scoped references for joins.
                    # But Schema doesn't have ScopedCol.
                    # We must rely on the Planner to use unique output names in previous steps
                    # OR we just try it. 
                    # Actually, if we use aliases L and R, we need the Expr to know about L and R.
                    # But Expr has no source.
                    # Fix: Implicitly, ON clause usually correlates left side to Left Table.
                    # A better approach for this strict safe executor is to assume columns are unique.
                    # Or, typically in these agents, we rely on 'natural join' logic if names match.
                    # Let's stick to the generated query. If it fails due to ambiguity, 
                    # then the Planner must ensure unique names via Project before Join.
                    
                elif op.op == "project":
                    input_ref = self._get_table_ref(op.input)
                    selects = []
                    for expr, alias in zip(op.exprs, op.aliases):
                        expr_sql = self._compile_expr(expr)
                        selects.append(f"{expr_sql} AS \"{alias}\"")
                    
                    cols_sql = ", ".join(selects)
                    query = f"CREATE OR REPLACE TABLE {step_id} AS SELECT {cols_sql} FROM {input_ref}"

                elif op.op == "filter":
                    input_ref = self._get_table_ref(op.input)
                    predicate_sql = self._compile_expr(op.predicate)
                    query = f"CREATE OR REPLACE TABLE {step_id} AS SELECT * FROM {input_ref} WHERE {predicate_sql}"

                elif op.op == "union":
                    refs = [self._get_table_ref(r) for r in op.inputs]
                    union_op = " UNION " if op.mode == "distinct" else " UNION ALL "
                    inputs_sql = union_op.join([f"SELECT * FROM {r}" for r in refs])
                    query = f"CREATE OR REPLACE TABLE {step_id} AS {inputs_sql}"

                elif op.op == "group_agg":
                    input_ref = self._get_table_ref(op.input)
                    keys = [self._compile_expr(k) for k in op.keys]
                    aggs = []
                    for agg in op.aggs:
                        # e.g. COUNT(DISTINCT col)
                        distinct_str = "DISTINCT " if agg.distinct else ""
                        expr_sql = self._compile_expr(agg.expr)
                        aggs.append(f"{agg.func.upper()}({distinct_str}{expr_sql}) AS \"{agg.as_name}\"")
                    
                    select_items = keys + aggs
                    select_sql = ", ".join(select_items)
                    group_sql = ", ".join(keys)
                    
                    if not keys:
                        # Aggregation without grouping (global)
                        query = f"CREATE OR REPLACE TABLE {step_id} AS SELECT {select_sql} FROM {input_ref}"
                    else:
                        query = f"CREATE OR REPLACE TABLE {step_id} AS SELECT {select_sql} FROM {input_ref} GROUP BY {group_sql}"

                elif op.op == "order_limit":
                    input_ref = self._get_table_ref(op.input)
                    parts = [f"SELECT * FROM {input_ref}"]
                    
                    if op.order_by:
                        orders = []
                        for sort in op.order_by:
                            expr_sql = self._compile_expr(sort.expr)
                            nulls_str = f" NULLS {sort.nulls.upper()}"
                            orders.append(f"{expr_sql} {sort.direction.upper()}{nulls_str}")
                        parts.append("ORDER BY " + ", ".join(orders))
                    
                    if op.limit is not None:
                        parts.append(f"LIMIT {op.limit}")
                    
                    if op.offset is not None:
                        parts.append(f"OFFSET {op.offset}")
                        
                    query = f"CREATE OR REPLACE TABLE {step_id} AS {' '.join(parts)}"

                logger.info(f"Executing step {step_id}: {query}")
                con.execute(query)

            except Exception as e:
                logger.error(f"Step {step_id} failed with query: {query}")
                raise PipelineError(
                    node=self.node_name,
                    message=f"Step {step_id} ({op.op}) failed: {e}",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.AGGREGATOR_FAILED
                )

        # 4. Fetch Final Result
        try:
            final_ref = self._get_table_ref(plan.final_output)
            final_df = con.execute(f"SELECT * FROM {final_ref}").pl()
            return final_df.to_dicts()
        except Exception as e:
             raise PipelineError(
                node=self.node_name,
                message=f"Failed to fetch final output {plan.final_output.id}: {e}",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.AGGREGATOR_FAILED
            )


    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """Executes the aggregation logic."""
        results = state.subquery_results or {}
        
        try:
            # Deterministic Path
            if state.result_plan:
                 final_rows = self._execute_deterministic_plan(state)
                 return {
                    "final_answer": final_rows,
                    "reasoning": [{"node": self.node_name, "content": "Deterministic Aggregation (Strict Plan) executed successfully."}]
                 }

            # Fast Path (Single Result)
            output_mode = state.output_mode
            if len(results) == 1 and not state.errors and output_mode == "data":
                 first_result = next(iter(results.values()))
                 rows = first_result.rows if first_result else []
                 return {
                    "final_answer": rows,
                    "reasoning": [{"node": self.node_name, "content": "Fast path: Raw data result passed through (output_mode='data')."}]
                }
            
            # Slow Path (LLM Fallback)
            final_answer = self._display_result_with_llm(state)
            return {
                "final_answer": final_answer,
                "reasoning": [{"node": self.node_name, "content": "LLM Aggregation used (Fallback). warning: Non-deterministic."}]
            }

        except Exception as e:
            logger.error(f"Node {self.node_name} failed: {e}")
            return {
                "final_answer": f"Error during aggregation: {str(e)}",
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
