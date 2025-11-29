from __future__ import annotations

import json
from typing import Callable, Optional, Union

from langchain_core.runnables import Runnable

from nl2sql.capabilities import EngineCapabilities, get_capabilities
from nl2sql.schemas import GeneratedSQL, GraphState, SQLModel
from nl2sql.nodes.generator.prompts import GENERATOR_PROMPT


class GeneratorNode:
    """
    SQL generator node with dialect awareness and guardrails.
    """

    def __init__(self, profile_engine: str, row_limit: int, llm: Optional[LLMCallable] = None):
        self.llm = llm
        self.profile_engine = profile_engine
        self.row_limit = row_limit

    def __call__(self, state: GraphState) -> GraphState:
        caps: EngineCapabilities = get_capabilities(self.profile_engine)
        if not state.plan:
            state.errors.append("No plan to generate SQL from.")
            return state

        # Enforce limit cap from profile
        if "limit" in state.plan:
            try:
                state.plan["limit"] = min(int(state.plan["limit"]), self.row_limit)
            except Exception:
                state.plan["limit"] = self.row_limit
        else:
            state.plan["limit"] = self.row_limit

        if not self.llm:
            state.errors.append("SQL generator LLM not provided; no SQL generated.")
            return state

        limit_guidance = {
            "limit": "Ensure the query uses 'LIMIT {n}'",
            "top_fetch": "use 'SELECT TOP {n}' or 'OFFSET/FETCH' as appropriate",
        }.get(caps.limit_syntax, "Ensure the query uses a safe LIMIT")

        error_context = ""
        if state.errors:
            error_context = f"\nPREVIOUS ERRORS (Fix these): {'; '.join(state.errors)}\n"

        prompt = GENERATOR_PROMPT.format(
            limit_guidance=limit_guidance,
            dialect=caps.dialect,
            plan_json=json.dumps(state.plan),
            error_context=error_context
        )
        
        try:
            if isinstance(self.llm, Runnable):
                sql_model = self.llm.invoke(prompt)
            else:
                sql_model = self.llm(prompt)
            
            sql = sql_model.sql
            lower_sql = sql.lower()
            if "select *" in lower_sql or ".*" in lower_sql:
                columns_json = state.validation.get("schema_columns")
                if columns_json and state.plan and len(state.plan.get("tables", [])) == 1:
                    try:
                        columns_map = json.loads(columns_json)
                        table_entry = state.plan["tables"][0]
                        table_name = table_entry.get("name")
                        alias = table_entry.get("alias")
                        cols = columns_map.get(table_name, [])
                        quote = caps.identifier_quote or '"'
                        col_exprs = []
                        for col in cols:
                            if alias:
                                col_exprs.append(f'{alias}.{quote}{col}{quote}')
                            else:
                                col_exprs.append(f'{quote}{table_name}{quote}.{quote}{col}{quote}')
                        if not col_exprs:
                            state.errors.append("Wildcard rejected and no columns available to expand.")
                            state.sql_draft = None
                            return state
                        table_clause = f'{quote}{table_name}{quote}'
                        if alias:
                            table_clause += f" AS {alias}"
                        order_clause = ""
                        if state.plan.get("order_by"):
                            ob = state.plan["order_by"][0]
                            order_clause = f' ORDER BY {ob.get("expr")} {ob.get("direction","asc").upper()}'
                        sql = f"SELECT {', '.join(col_exprs)} FROM {table_clause}{order_clause} LIMIT {state.plan.get('limit', self.row_limit)}"
                    except Exception:
                        state.errors.append("Wildcard rejected and column expansion failed.")
                        state.sql_draft = None
                        return state
                else:
                    state.errors.append("Wildcard select rejected (SELECT * or table.*).")
                    state.sql_draft = None
                    return state
            # Enforce ORDER BY presence when plan specifies it
            if state.plan.get("order_by") and "order by" not in lower_sql:
                ob = state.plan["order_by"][0]
                dir_val = ob.get("direction", "asc").upper()
                order_clause = f" ORDER BY {ob.get('expr')} {dir_val}"
                
                limit_idx = lower_sql.rfind("limit")
                if limit_idx != -1:
                    # Insert before LIMIT
                    # Ensure we don't break the syntax (add space)
                    sql = sql[:limit_idx] + order_clause + " " + sql[limit_idx:]
                else:
                    sql = f"{sql.rstrip(';')} {order_clause}"
            # Enforce LIMIT is within row_limit
            if "limit" in lower_sql:
                try:
                    parts = lower_sql.split("limit")
                    if len(parts) > 1:
                        limit_val = parts[-1].strip().split()[0]
                        lim = int(limit_val)
                        if lim > self.row_limit:
                            state.errors.append(f"Limit {lim} exceeds allowed {self.row_limit}.")
                            state.sql_draft = None
                            return state
                    else:
                        state.errors.append("Could not parse LIMIT value.")
                        state.sql_draft = None
                        return state
                except Exception:
                    state.errors.append("Could not parse LIMIT value.")
                    state.sql_draft = None
                    return state
            state.sql_draft = GeneratedSQL(
                sql=sql,
                rationale=sql_model.rationale or "LLM-generated SQL",
                limit_enforced=bool(sql_model.limit_enforced or ("limit" in sql.lower()) or (" top " in sql.lower()) or (" fetch " in sql.lower())),
                draft_only=bool(sql_model.draft_only) if sql_model.draft_only is not None else False,
            )
        except Exception as exc:
            state.sql_draft = None
            state.errors.append(f"SQL generation failed: {exc}")
        return state
