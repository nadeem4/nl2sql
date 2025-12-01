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
        # 1. Check alias usage
        if col.alias and context != "select_columns":
            errors.append(f"Column alias '{col.alias}' used in '{context}'. Aliases are only allowed in 'select_columns'.")

        # 2. Validate expression
        if col.is_derived:
            # For derived columns, we skip strict schema validation for now.
            pass
        else:
            # For normal columns, 'expr' must match a schema column exactly (e.g., "t1.name")
            if col.expr not in schema_cols:
                # Try to give a helpful error message
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

        # 0. Security Check: Query Type
        query_type = state.plan.get("query_type", "READ")
        if query_type != "READ":
            state.errors.append(f"Security Violation: Query type '{query_type}' is not allowed. Only READ queries are permitted.")
            return state

        # Convert dict plan to PlanModel if needed (it should be a dict in state.plan)
        # But we want to work with the Pydantic model for easier access if possible,
        # or just work with the dict. The PlannerNode puts `plan_model.model_dump()` into `state.plan`.
        # So `state.plan` is a dict.
        # Let's try to parse it back to PlanModel for validation logic, or just access as dict.
        # Accessing as dict is safer if the structure is slightly off, but Pydantic gives us type safety.
        # Let's use Pydantic parsing to also validate structure.
        try:
            plan = PlanModel(**state.plan)
        except Exception as e:
            state.errors.append(f"Invalid plan structure: {e}")
            return state

        # Build schema lookup maps
        # SchemaInfo tables have aliases assigned by SchemaNode.
        # We assume the Planner used these aliases.
        schema_cols = set()
        
        # 1. Validate Tables and Aliases
        plan_table_aliases = set()
        plan_table_names = set()
        for t in plan.tables:
            plan_table_aliases.add(t.alias)
            plan_table_names.add(t.name)
            
            # Find matching table in schema
            found = False
            for st in state.schema_info.tables:
                if st.name == t.name and st.alias == t.alias:
                    schema_cols.update(st.columns) # st.columns are already "t1.col"
                    found = True
                    break
            
            if not found:
                 state.errors.append(f"Table '{t.name}' with alias '{t.alias}' not found in schema or alias mismatch.")

        # 2. Validate Select Columns
        for col in plan.select_columns:
            self.validate_column_ref(col, schema_cols, plan_table_aliases, "select_columns", state.errors)

        # 3. Validate Filters
        for flt in plan.filters:
            self.validate_column_ref(flt.column, schema_cols, plan_table_aliases, "filters", state.errors)

        # 4. Validate Group By
        for gb in plan.group_by:
            self.validate_column_ref(gb, schema_cols, plan_table_aliases, "group_by", state.errors)

        # 5. Validate Order By
        for ob in plan.order_by:
            self.validate_column_ref(ob.column, schema_cols, plan_table_aliases, "order_by", state.errors)

        # 6. Validate Joins
        for join in plan.joins:
            if join.left not in plan_table_names:
                 state.errors.append(f"Join left table '{join.left}' is not in plan tables.")
            if join.right not in plan_table_names:
                 state.errors.append(f"Join right table '{join.right}' is not in plan tables.")
            
            # Validate ON clause (simple check)
            if not join.on:
                state.errors.append(f"Join between '{join.left}' and '{join.right}' has no ON clause.")

        # 7. Validate Having
        for hav in plan.having:
            if not hav.expr:
                state.errors.append(f"Having clause missing expression: {hav}")

        return state
            

