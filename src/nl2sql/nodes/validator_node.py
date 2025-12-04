from __future__ import annotations

import json
from typing import Callable, Optional, Set, Dict

from nl2sql.schemas import GraphState, ColumnRef, PlanModel, SchemaInfo

LLMCallable = Callable[[str], str]


class ValidatorNode:
    """
    Validates the generated execution plan against the schema and security policies.

    Ensures that:
    - The query type is allowed (READ only).
    - Tables and columns exist in the schema.
    - Aliases are used correctly.
    - No forbidden operations are present.
    """

    def __init__(self, row_limit: int | None = None):
        """
        Initializes the ValidatorNode.

        Args:
            row_limit: Optional maximum number of rows allowed (not strictly enforced here but good context).
        """
        self.row_limit = row_limit

    def is_aggregation(self, expr: str) -> bool:
        """Checks if an expression looks like an aggregation."""
        upper_expr = expr.upper()
        aggs = ["COUNT(", "SUM(", "AVG(", "MIN(", "MAX("]
        return any(agg in upper_expr for agg in aggs)

    def validate_aggregations(self, plan: PlanModel, errors: list[str]) -> None:
        """
        Validates GROUP BY logic.
        
        Rules:
        1. If there are aggregations in SELECT, all non-aggregated columns must be in GROUP BY.
        2. If there is a GROUP BY, all non-aggregated columns in SELECT must be in GROUP BY.
        """
        has_aggregations = False
        non_aggregated_cols = []
        
        for col in plan.select_columns:
            if col.is_derived or self.is_aggregation(col.expr):
                has_aggregations = True
            else:
                non_aggregated_cols.append(col)
                
        group_by_exprs = {gb.expr for gb in plan.group_by}
        
        if has_aggregations or plan.group_by:
            for col in non_aggregated_cols:
                if col.expr not in group_by_exprs:
                    errors.append(f"Column '{col.expr}' is selected but not aggregated or included in GROUP BY.")

    def validate_column_ref(self, col: ColumnRef, schema_cols: Set[str], plan_table_aliases: Set[str], context: str, errors: list[str]) -> None:
        """
        Validates a column reference.

        Args:
            col: The ColumnRef object to validate.
            schema_cols: Set of valid column expressions from the schema (e.g., "t1.name").
            plan_table_aliases: Set of table aliases defined in the plan.
            context: The context in which the column is used (e.g., "select_columns").
            errors: List to append error messages to.
        """
        if col.alias and context != "select_columns":
            errors.append(f"Column alias '{col.alias}' used in '{context}'. Aliases are only allowed in 'select_columns'.")

        if col.is_derived:
            pass
        else:
            if col.expr not in schema_cols:
                errors.append(f"Column '{col.expr}' not found in schema. Ensure you are using the pre-aliased name (e.g., 't1.col').")

    def __call__(self, state: GraphState) -> GraphState:
        """
        Executes the validation step.

        Args:
            state: The current graph state.

        Returns:
            The updated graph state with validation errors (if any).
        """
        # Clear errors from previous runs
        state.errors = []

        if not state.plan:
            state.errors.append("No plan to validate.")
            return state

        if not state.schema_info:
            return state

        query_type = state.plan.get("query_type", "READ")
        if query_type != "READ":
            state.errors.append(f"Security Violation: Query type '{query_type}' is not allowed. Only READ queries are permitted.")
            return state

        try:
            plan = PlanModel(**state.plan)
        except Exception as e:
            state.errors.append(f"Invalid plan structure: {e}")
            return state

        schema_cols = set()
        
        plan_table_aliases = set()
        plan_table_names = set()
        for t in plan.tables:
            plan_table_aliases.add(t.alias)
            plan_table_names.add(t.name)
            
            found = False
            for st in state.schema_info.tables:
                if st.name == t.name and st.alias == t.alias:
                    schema_cols.update(st.columns) 
                    found = True
                    break
            
            if not found:
                 state.errors.append(f"Table '{t.name}' with alias '{t.alias}' not found in schema or alias mismatch.")

        for col in plan.select_columns:
            self.validate_column_ref(col, schema_cols, plan_table_aliases, "select_columns", state.errors)

        for flt in plan.filters:
            self.validate_column_ref(flt.column, schema_cols, plan_table_aliases, "filters", state.errors)

        for gb in plan.group_by:
            self.validate_column_ref(gb, schema_cols, plan_table_aliases, "group_by", state.errors)

        for ob in plan.order_by:
            self.validate_column_ref(ob.column, schema_cols, plan_table_aliases, "order_by", state.errors)

        for join in plan.joins:
            if join.left not in plan_table_names:
                 state.errors.append(f"Join left table '{join.left}' is not in plan tables.")
            if join.right not in plan_table_names:
                 state.errors.append(f"Join right table '{join.right}' is not in plan tables.")
            
            if not join.on:
                state.errors.append(f"Join between '{join.left}' and '{join.right}' has no ON clause.")

        for hav in plan.having:
            if not hav.expr:
                state.errors.append(f"Having clause missing expression: {hav}")

        self.validate_aggregations(plan, state.errors)

        if "validator" not in state.thoughts:
            state.thoughts["validator"] = []
            
        if state.errors:
            state.thoughts["validator"].append("Validation Failed")
            for err in state.errors:
                state.thoughts["validator"].append(f"Error: {err}")
        else:
            state.thoughts["validator"].append("Validation Successful")
            state.thoughts["validator"].append("Plan is valid against schema and security policies.")

        return state
            

