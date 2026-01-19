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



    def _compile_expr(self, expr: Expr, scope: Optional[str] = None) -> str:
        """Compiles a Typed Expr to a SQL string fragment for DuckDB."""
        if expr.type == "col":
            # Using double quotes for column identifier safety
            ident = f'"{expr.name}"'
            return f"{scope}.{ident}" if scope else ident
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
            left = self._compile_expr(expr.left, scope=scope)
            right = self._compile_expr(expr.right, scope=scope)
            return f"({left} {expr.op} {right})"
        
        raise ValueError(f"Unknown expression type: {expr}")

    def _validate_identifier(self, ident: str) -> str:
        """Strictly validates that an identifier is safe for SQL interpolation.
        
        Allows only alphanumeric characters, underscores, and hyphens.
        """
        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', ident):
             raise ValueError(f"Invalid identifier detected: '{ident}'. Only alphanumeric, '_', and '-' allowed.")
        return ident

    def _get_table_ref(self, ref: RelationRef) -> str:
        """Resolves a RelationRef to a SQL table name."""
        return self._validate_identifier(ref.id)

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
        
        # 2. Register SubQuery Results (Strict Validation)
        # Ensure all planned subqueries are present. 
        # We rely on sub_queries in state to know what was expected.
        expected_sqs = {sq.id: sq for sq in (state.sub_queries or [])}
        
        for sq_id in expected_sqs:
            if sq_id not in results:
                raise PipelineError(
                    node=self.node_name,
                    message=f"Missing execution result for SubQuery {sq_id}. Partial failure detected.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.EXECUTION_FAILED
                )
            
            exec_model = results[sq_id]
            if not exec_model or not exec_model.rows:
                 # Strictly handle empty results vs valid empty sets? 
                 # If exec_model exists but rows is empty, it's a valid empty result.
                 df = pl.DataFrame([]) 
            else:
                df = pl.DataFrame(exec_model.rows)
            
            # Validate ID before registration, though UUIDs should be safe.
            safe_id = self._validate_identifier(sq_id)
            con.register(safe_id, df)

        # 3. Execute Plan Steps
        for step in plan.steps:
            op = step.operation
            step_id = self._validate_identifier(step.step_id)
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
                        # Hardening: Assume planner puts left column in left.
                        # We use explicit L and R aliases in the JOIN below.
                        l_sql = self._compile_expr(bin_op.left, scope="L")
                        r_sql = self._compile_expr(bin_op.right, scope="R")
                        on_clauses.append(f"({l_sql} {bin_op.op} {r_sql})")
                    
                    on_sql = " AND ".join(on_clauses)
                    join_type = op.join_type.upper()
                    
                    query = f"""
                        CREATE OR REPLACE TABLE {step_id} AS 
                        SELECT L.*, R.* EXCLUDE ({", ".join([f'"{c.name}"' for c in (op.on_columns or [])]) if op.on_columns else ""})
                        FROM {left_ref} AS L
                        {join_type} JOIN {right_ref} AS R 
                        ON {on_sql}
                    """
                    # Use EXCLUDE if we have on_columns to prevent duplicate keys in result table,
                    # though DuckDB handles 'SELECT *' from joins with duplicate names by renaming.
                    # A cleaner way is just SELECT L.*, R.* and let DuckDB rename if ambiguous.
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
            # Strict Path (Only deterministic)
            if not state.result_plan:
                 raise PipelineError(
                     node=self.node_name,
                     message="No ResultPlan found. Aggregation cannot proceed.",
                     severity=ErrorSeverity.ERROR,
                     error_code=ErrorCode.PLANNER_FAILED
                 )

            final_rows = self._execute_deterministic_plan(state)
            return {
                "final_answer": final_rows,
                "reasoning": [{"node": self.node_name, "content": "Deterministic Aggregation (Strict Plan) executed successfully."}]
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
