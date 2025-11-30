from __future__ import annotations

import json
from typing import Optional

import sqlglot
from sqlglot import expressions as exp

from nl2sql.capabilities import EngineCapabilities, get_capabilities
from nl2sql.schemas import GeneratedSQL, GraphState, SQLModel


class GeneratorNode:
    """
    SQL generator node with dialect awareness and guardrails.
    """

    def __init__(self, profile_engine: str, row_limit: int):
        self.profile_engine = profile_engine
        self.row_limit = row_limit

    def __call__(self, state: GraphState) -> GraphState:
        caps: EngineCapabilities = get_capabilities(self.profile_engine)
        if not state.plan:
            state.errors.append("No plan to generate SQL from.")
            return state

        # Enforce limit cap from profile
        limit = self.row_limit
        if state.plan.get("limit"):
            try:
                limit = min(int(state.plan["limit"]), self.row_limit)
            except Exception:
                pass
        
        try:
            final_sql = self._generate_sql_from_plan(state.plan, limit)
            
            state.sql_draft = GeneratedSQL(
                sql=final_sql,
                rationale="Rule-based generation via sqlglot",
                limit_enforced=True,
                draft_only=False
            )

        except Exception as exc:
            state.sql_draft = None
            state.errors.append(f"SQL generation failed: {exc}")
        
        return state

    def _generate_sql_from_plan(self, plan: dict, limit: int) -> str:
        tables = plan.get("tables", [])
        if not tables:
            raise ValueError("No tables in plan.")
        
        query = exp.select()
        
        # 1. SELECT
        query = self._build_select(query, plan)

        # 2. FROM
        query = self._build_from(query, tables[0])

        # 3. JOINS
        query = self._build_joins(query, plan, tables)

        # 4. WHERE
        query = self._build_where(query, plan)

        # 5. GROUP BY
        query = self._build_group_by(query, plan)

        # 6. HAVING
        query = self._build_having(query, plan)
        
        # 7. ORDER BY
        query = self._build_order_by(query, plan)

        # 8. LIMIT
        query = query.limit(limit)

        # Transpile
        dialect_map = {
            "postgresql": "postgres",
            "mssql": "tsql",
        }
        target_dialect = dialect_map.get(self.profile_engine, self.profile_engine)
        
        final_sql = query.sql(dialect=target_dialect)
        return final_sql

    def _to_col(self, col_ref: dict) -> exp.Column:
        return exp.Column(
            this=exp.Identifier(this=col_ref["name"], quoted=False),
            table=exp.Identifier(this=col_ref["alias"], quoted=False)
        )

    def _build_select(self, query: exp.Select, plan: dict) -> exp.Select:
        select_cols = plan.get("select_columns", [])
        aggregates = plan.get("aggregates", [])
        
        if not select_cols and not aggregates:
            # Fallback if no columns specified (shouldn't happen with valid plan)
            pass

        for col in select_cols:
            query = query.select(self._to_col(col))
        
        for agg in aggregates:
            expr = sqlglot.parse_one(agg["expr"])
            if agg.get("alias"):
                expr = exp.Alias(this=expr, alias=exp.Identifier(this=agg["alias"], quoted=False))
            query = query.select(expr)
        
        return query

    def _build_from(self, query: exp.Select, main_table: dict) -> exp.Select:
        main_tbl_exp = sqlglot.to_table(main_table["name"])
        if main_table.get("alias"):
            main_tbl_exp.set("alias", exp.TableAlias(this=exp.Identifier(this=main_table["alias"], quoted=False)))
        return query.from_(main_tbl_exp)

    def _build_joins(self, query: exp.Select, plan: dict, tables: list) -> exp.Select:
        for join in plan.get("joins", []):
            right_tbl_name = join["right"]
            right_alias = None
            for t in tables:
                if t["name"] == right_tbl_name:
                    right_alias = t.get("alias")
                    break
            
            join_tbl_exp = sqlglot.to_table(right_tbl_name)
            if right_alias:
                join_tbl_exp.set("alias", exp.TableAlias(this=exp.Identifier(this=right_alias, quoted=False)))
            
            on_cond = None
            if join.get("on"):
                on_cond = sqlglot.parse_one(join["on"][0])
                for extra_on in join["on"][1:]:
                    on_cond = exp.And(this=on_cond, expression=sqlglot.parse_one(extra_on))
            
            query = query.join(join_tbl_exp, on=on_cond, kind=join.get("join_type", "inner"))
        return query

    def _build_where(self, query: exp.Select, plan: dict) -> exp.Select:
        filters = plan.get("filters", [])
        if filters:
            where_cond = None
            for flt in filters:
                col = self._to_col(flt["column"])
                val = flt["value"]
                op = flt["op"].upper()
                
                # Handle value literal
                if isinstance(val, str):
                    val_exp = exp.Literal.string(val)
                elif isinstance(val, (int, float)):
                    val_exp = exp.Literal.number(val)
                elif isinstance(val, bool):
                    val_exp = exp.Boolean(this=val)
                else:
                    val_exp = exp.Literal.string(str(val))

                # Construct expression based on op
                if op == "=": cond = exp.EQ(this=col, expression=val_exp)
                elif op == "!=" or op == "<>": cond = exp.NEQ(this=col, expression=val_exp)
                elif op == ">": cond = exp.GT(this=col, expression=val_exp)
                elif op == "<": cond = exp.LT(this=col, expression=val_exp)
                elif op == ">=": cond = exp.GTE(this=col, expression=val_exp)
                elif op == "<=": cond = exp.LTE(this=col, expression=val_exp)
                elif "LIKE" in op: cond = exp.Like(this=col, expression=val_exp)
                elif "IN" in op: 
                    cond = exp.In(this=col, expression=val_exp)
                else:
                    # Fallback for BETWEEN and others
                    cond = sqlglot.parse_one(f"{col.sql()} {op} {val}")
                
                if where_cond:
                    logic = flt.get("logic", "and").lower()
                    if logic == "or":
                        where_cond = exp.Or(this=where_cond, expression=cond)
                    else:
                        where_cond = exp.And(this=where_cond, expression=cond)
                else:
                    where_cond = cond
            
            query = query.where(where_cond)
        return query

    def _build_group_by(self, query: exp.Select, plan: dict) -> exp.Select:
        for gb in plan.get("group_by", []):
            query = query.group_by(self._to_col(gb))
        return query

    def _build_having(self, query: exp.Select, plan: dict) -> exp.Select:
        having = plan.get("having", [])
        if having:
            having_cond = None
            for hav in having:
                col = sqlglot.parse_one(hav["expr"])
                val = hav["value"]
                op = hav["op"].upper()
                
                # Handle value literal
                if isinstance(val, str):
                    val_exp = exp.Literal.string(val)
                elif isinstance(val, (int, float)):
                    val_exp = exp.Literal.number(val)
                elif isinstance(val, bool):
                    val_exp = exp.Boolean(this=val)
                else:
                    val_exp = exp.Literal.string(str(val))

                # Construct expression based on op
                if op == "=": cond = exp.EQ(this=col, expression=val_exp)
                elif op == "!=" or op == "<>": cond = exp.NEQ(this=col, expression=val_exp)
                elif op == ">": cond = exp.GT(this=col, expression=val_exp)
                elif op == "<": cond = exp.LT(this=col, expression=val_exp)
                elif op == ">=": cond = exp.GTE(this=col, expression=val_exp)
                elif op == "<=": cond = exp.LTE(this=col, expression=val_exp)
                elif "LIKE" in op: cond = exp.Like(this=col, expression=val_exp)
                elif "IN" in op: 
                    cond = exp.In(this=col, expression=val_exp)
                else:
                    cond = sqlglot.parse_one(f"{hav['expr']} {op} {hav['value']}")
                
                if having_cond:
                    logic = hav.get("logic", "and").lower()
                    if logic == "or":
                        having_cond = exp.Or(this=having_cond, expression=cond)
                    else:
                        having_cond = exp.And(this=having_cond, expression=cond)
                else:
                    having_cond = cond
            
            query = query.having(having_cond)
        return query

    def _build_order_by(self, query: exp.Select, plan: dict) -> exp.Select:
        for ob in plan.get("order_by", []):
            query = query.order_by(self._to_col(ob["column"]), desc=(ob["direction"].lower() == "desc"))
        return query
