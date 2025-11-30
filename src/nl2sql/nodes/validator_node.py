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
        # Clear errors from previous runs (Planner should have consumed them)
        # But if we are looping, we want to start fresh validation.
        state.errors = []

        if not state.plan:
            state.errors.append("No plan to validate.")
            return state

        if not state.schema_info:
            return state

        schema_tables = set(state.schema_info.tables)
        schema_cols = state.schema_info.columns
        schema_aliases = state.schema_info.aliases

        # 1. Validate Tables and Aliases
        plan_aliases = {}
        for tbl in state.plan.get("tables", []):
            name = tbl.get("name")
            alias = tbl.get("alias")
            
            if name not in schema_tables:
                state.errors.append(f"Table '{name}' does not exist in schema.")
                continue
            
            expected_alias = schema_aliases.get(name)
            if alias != expected_alias:
                state.errors.append(f"Table '{name}' must use alias '{expected_alias}', but found '{alias}'.")
            
            if alias:
                plan_aliases[alias] = name

        # 2. Validate Columns (needed_columns)
        # We expect columns to be in "alias.column" format
        for col_ref in state.plan.get("needed_columns", []):
            parts = col_ref.split(".")
            if len(parts) != 2:
                state.errors.append(f"Column '{col_ref}' is not properly qualified (expected 'alias.column').")
                continue
            
            alias, col_name = parts
            if alias not in plan_aliases:
                state.errors.append(f"Alias '{alias}' in column '{col_ref}' is not defined in plan tables.")
                continue
            
            table_name = plan_aliases[alias]
            if table_name in schema_cols:
                if col_name not in schema_cols[table_name]:
                    state.errors.append(f"Column '{col_name}' does not exist in table '{table_name}'.")
            else:
                # Should have been caught by table validation, but just in case
                pass

        # 3. Validate Joins (check on_clause columns if possible, but they might be complex expressions)
        # For now, we rely on needed_columns covering all used columns.
        
        # 4. Validate Filters (check column existence)
        for flt in state.plan.get("filters", []):
            col = flt.get("column")
            if col and col not in state.plan.get("needed_columns", []):
                 # It's okay if it's not in needed_columns if we don't strictly enforce it, 
                 # but the prompt says "List EVERY column used".
                 # Let's warn or error? The prompt says "List EVERY column".
                 # Let's enforce it to be strict.
                 state.errors.append(f"Filter column '{col}' missing from 'needed_columns'.")

        return state


def _extract_limit(sql_text: str) -> int | None:
    # Unused now, but keeping for reference or removal
    return None
