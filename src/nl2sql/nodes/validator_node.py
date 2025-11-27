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
        if not state.sql_draft:
            state.errors.append("No SQL to validate.")
            return state
        sql_text = state.sql_draft["sql"]
        sql_lower = sql_text.lower()
        # Validate plan tables exist in schema listing when available
        schema_tables = set()
        if state.validation.get("schema_tables"):
            schema_tables = {t.strip() for t in state.validation["schema_tables"].split(",")}
        if state.plan and state.plan.get("tables") and schema_tables:
            missing = []
            for tbl in state.plan["tables"]:
                name = tbl.get("name")
                if name and name not in schema_tables:
                    missing.append(name)
            if missing:
                state.errors.append(f"Plan references missing tables: {', '.join(sorted(missing))}.")
                state.sql_draft = None
                return state
        # Validate columns against schema when available
        schema_cols = {}
        if state.validation.get("schema_columns"):
            try:
                schema_cols = json.loads(state.validation["schema_columns"])
            except Exception:
                schema_cols = {}

        if any(term in sql_lower for term in ["insert ", "update ", "delete ", "drop ", "alter "]):
            state.errors.append("Write/DML detected; blocked.")
        if "limit" not in sql_lower:
            state.errors.append("Missing LIMIT in SQL.")
        if "?" in sql_text:
            state.errors.append("Parameter placeholders detected; inline literals instead.")
            state.sql_draft = None
            return state
        try:
            parsed = sqlglot.parse_one(sql_text)
        except Exception:
            state.errors.append("SQL parse failed; blocked.")
            return state
        # validate column references
        if schema_cols:
            invalid_cols = []
            for col in parsed.find_all(exp.Column):
                tbl = col.table
                col_name = col.name
                if tbl and tbl in schema_cols:
                    if col_name not in schema_cols[tbl]:
                        invalid_cols.append(f"{tbl}.{col_name}")
            if invalid_cols:
                state.errors.append(f"References missing columns: {', '.join(sorted(set(invalid_cols)))}")
                state.sql_draft = None
                return state
        # block UNION
        if parsed.find(exp.Union):
            state.errors.append("UNION detected; blocked.")
        # enforce ORDER BY if plan has it
        if state.plan and state.plan.get("order_by"):
            has_order = bool(parsed.args.get("order"))
            if not has_order:
                state.errors.append("Plan requested ORDER BY but SQL missing it.")
        # enforce LIMIT numeric and within row_limit
        limit_val = _extract_limit(sql_text)
        if limit_val is None:
            state.errors.append("Could not parse LIMIT value.")
        elif self.row_limit is not None and limit_val > self.row_limit:
            state.errors.append(f"Limit {limit_val} exceeds allowed {self.row_limit}.")
        return state


def _extract_limit(sql_text: str) -> int | None:
    try:
        parsed = sqlglot.parse_one(sql_text)
    except Exception:
        return None
    limit = parsed.args.get("limit")
    if not limit:
        return None
    try:
        expr = limit.expression if hasattr(limit, "expression") else limit
        return int(expr.this)
    except Exception:
        return None
