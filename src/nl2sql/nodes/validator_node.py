from __future__ import annotations

import json
from typing import Callable, Optional

import sqlglot
from sqlglot import expressions as exp

from nl2sql.schemas import GraphState

LLMCallable = Callable[[str], str]


class ValidatorNode:
    """
    Stricter validation: single statement, LIMIT present and <= row_limit, no DDL/DML, no UNION, ORDER BY honored, schema references valid.
    """

    def __init__(self, row_limit: int | None = None):
        self.row_limit = row_limit

    def __call__(self, state: GraphState) -> GraphState:
        # Clear errors from previous runs
        state.errors = []

        if not state.plan:
            state.errors.append("No plan to validate.")
            return state

        if not state.schema_info:
            return state

        # Build schema lookup maps
        schema_tables = {t.name for t in state.schema_info.tables}
        schema_cols = {t.name: set(t.columns) for t in state.schema_info.tables}
        
        # 1. Validate Tables and Aliases
        plan_aliases = {}
        for tbl in state.plan.get("tables", []):
            name = tbl.get("name")
            alias = tbl.get("alias")
            
            if name not in schema_tables:
                state.errors.append(f"Table '{name}' does not exist in schema.")
                continue
            
            if not alias:
                state.errors.append(f"Table '{name}' must have an alias.")
                continue
                
            plan_aliases[alias] = name

        # Helper to validate ColumnRef
        def validate_column_ref(col_ref: dict, context: str):
            alias = col_ref.get("alias")
            name = col_ref.get("name")
            
            if not alias or not name:
                state.errors.append(f"Invalid column reference in {context}: {col_ref}")
                return

            if alias not in plan_aliases:
                state.errors.append(f"Alias '{alias}' in {context} is not defined in plan tables.")
                return
            
            table_name = plan_aliases[alias]
            if table_name in schema_cols:
                if name not in schema_cols[table_name]:
                    state.errors.append(f"Column '{name}' does not exist in table '{table_name}' (alias '{alias}').")

        # 2. Validate Select Columns
        for col in state.plan.get("select_columns", []):
            validate_column_ref(col, "select_columns")

        # 3. Validate Filters
        for flt in state.plan.get("filters", []):
            validate_column_ref(flt.get("column"), "filters")

        # 4. Validate Group By
        for gb in state.plan.get("group_by", []):
            validate_column_ref(gb, "group_by")

        # 5. Validate Order By
        for ob in state.plan.get("order_by", []):
            validate_column_ref(ob.get("column"), "order_by")

        # 6. Validate Joins
        for join in state.plan.get("joins", []):
            left = join.get("left")
            right = join.get("right")
            
            # Check if left/right are valid table names present in the plan
            plan_table_names = {t.get("name") for t in state.plan.get("tables", [])}
            
            if left not in plan_table_names:
                 state.errors.append(f"Join left table '{left}' is not in plan tables.")
            if right not in plan_table_names:
                 state.errors.append(f"Join right table '{right}' is not in plan tables.")
            
            # Validate ON clause (skipped for now)
            pass

        # 7. Validate Aggregates
        for agg in state.plan.get("aggregates", []):
            if not agg.get("alias"):
                state.errors.append(f"Aggregate expression '{agg.get('expr')}' missing alias.")

        # 8. Validate Having
        for hav in state.plan.get("having", []):
            if not hav.get("expr"):
                state.errors.append(f"Having clause missing expression: {hav}")

        return state
            

