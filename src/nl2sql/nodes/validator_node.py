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
            if not state.errors:
                state.errors.append("No SQL to validate.")
            return state

        # Clear previous validation errors to ensure fresh validation
        state.errors = []
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
            # Flatten all valid columns for lookup of unqualified names
            all_valid_cols = set()
            for cols in schema_cols.values():
                all_valid_cols.update(cols)
            
            for col in parsed.find_all(exp.Column):
                tbl = col.table
                col_name = col.name
                
                # Skip wildcard
                if col_name == "*":
                    continue

                if tbl:
                    # Qualified column: tbl.col
                    if tbl in schema_cols:
                        if col_name not in schema_cols[tbl]:
                            invalid_cols.append(f"{tbl}.{col_name}")
                    # If table not in schema_cols, we might have missed it in retrieval or it's an alias
                    # For now, we only strictly validate if the table is KNOWN in the schema context
                else:
                    # Unqualified column: col
                    # Must exist in at least one of the tables in the context
                    if col_name not in all_valid_cols:
                        # It might be an alias defined in the query (e.g. SELECT count(*) as cnt ... ORDER BY cnt)
                        # sqlglot can help, but for now let's be strict on schema columns.
                        # To avoid false positives on aliases, we could check if it's in the projection aliases?
                        # But simpler: if it's not in schema, flag it.
                        # Wait, what about aliases? "SELECT price AS p FROM products ORDER BY p"
                        # 'p' is not in schema.
                        # We should check if it's an alias defined in the query.
                        # For now, let's just check if it's in all_valid_cols.
                        # If not, we risk false positives on aliases.
                        # Let's try to exclude aliases.
                        pass 
                        # Actually, let's just check against schema for now. 
                        # If it's a complex query with aliases, this might be too strict.
                        # But for "SELECT age FROM users", 'age' is definitely wrong.
                        
                        # Let's check if it's an alias in the same query scope?
                        # sqlglot's optimizer.scope.build_scope can help but might be heavy.
                        # Let's stick to: if it's not in schema, AND it's not an alias...
                        # How to check alias easily?
                        # parsed.aliases returns aliases in the SELECT clause.
                        
                        is_alias = False
                        # Check if this column name matches any alias in the SELECT list
                        for expression in parsed.find_all(exp.Alias):
                            if expression.alias == col_name:
                                is_alias = True
                                break
                        
                        if not is_alias and col_name not in all_valid_cols:
                             invalid_cols.append(f"{col_name} (unqualified)")

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
