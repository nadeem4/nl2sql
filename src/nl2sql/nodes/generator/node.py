from __future__ import annotations

import json
from typing import Callable, Optional, Union

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.runnables import Runnable
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nl2sql.capabilities import EngineCapabilities, get_capabilities
from nl2sql.json_utils import strip_code_fences
from nl2sql.schemas import GeneratedSQL, GraphState
from nl2sql.nodes.generator.prompts import GENERATOR_PROMPT

LLMCallable = Union[Callable[[str], str], Runnable]


class SQLModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str
    rationale: Optional[str] = None
    limit_enforced: Optional[bool] = None
    draft_only: Optional[bool] = None


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
            "limit": "append 'LIMIT {n}'",
            "top_fetch": "use 'SELECT TOP {n}' or 'OFFSET/FETCH' as appropriate",
        }.get(caps.limit_syntax, "append a safe LIMIT")

        parser = PydanticOutputParser(pydantic_object=SQLModel)
        
        error_context = ""
        if state.errors:
            error_context = f"\nPREVIOUS ERRORS (Fix these): {'; '.join(state.errors)}\n"

        prompt = GENERATOR_PROMPT.format(
            format_instructions=parser.get_format_instructions(),
            limit_guidance=limit_guidance,
            dialect=caps.dialect,
            plan_json=json.dumps(state.plan),
            error_context=error_context
        )
        
        if isinstance(self.llm, Runnable):
            raw = self.llm.invoke(prompt)
        else:
            raw = self.llm(prompt)
            
        raw_str = raw.content if hasattr(raw, "content") else (raw.strip() if isinstance(raw, str) else str(raw))
        
        try:
            sql_model = parser.parse(strip_code_fences(raw_str))
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
                sql = f"{sql.rstrip(';')} ORDER BY {ob.get('expr')} {dir_val}"
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
        except ValidationError as exc:
            state.sql_draft = None
            state.errors.append(f"SQL generation parse failed: {exc}")
        return state
