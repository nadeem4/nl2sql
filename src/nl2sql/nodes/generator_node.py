from __future__ import annotations

import json
from typing import Optional

import sqlglot
from sqlglot import expressions as exp

from nl2sql.capabilities import EngineCapabilities, get_capabilities
from nl2sql.schemas import GraphState


from nl2sql.datasource_registry import DatasourceRegistry


class GeneratorNode:
    """
    SQL generator node with dialect awareness and guardrails.

    Converts the abstract `PlanModel` into a concrete SQL query string,
    handling dialect-specific syntax and enforcing row limits.
    """

    def __init__(self, registry: DatasourceRegistry):
        """
        Initializes the GeneratorNode.

        Args:
            registry: Datasource registry for accessing profiles.
        """
        self.registry = registry

    def __call__(self, state: GraphState) -> GraphState:
        """
        Executes the SQL generation step.

        Args:
            state: The current graph state.

        Returns:
            The updated graph state with the generated SQL draft.
        """
        if not state.datasource_id:
            state.errors.append("No datasource_id in state. Router must run before GeneratorNode.")
            return state

        profile = self.registry.get_profile(state.datasource_id)
        self.profile_engine = profile.engine
        self.row_limit = profile.row_limit

        caps: EngineCapabilities = get_capabilities(self.profile_engine)
        if not state.plan:
            state.errors.append("No plan to generate SQL from.")
            return state

        limit = self.row_limit
        if state.plan.get("limit"):
            try:
                limit = min(int(state.plan["limit"]), self.row_limit)
            except Exception:
                pass
        
        try:
            final_sql = self._generate_sql_from_plan(state.plan, limit)
            
            if "generator" not in state.thoughts:
                state.thoughts["generator"] = []
            
            state.thoughts["generator"].append(f"Generated SQL: {final_sql}")
            state.thoughts["generator"].append(f"Rationale: {state.plan.get('reasoning', 'N/A')}")
            
            state.sql_draft = final_sql

        except Exception as exc:
            state.sql_draft = None
            state.errors.append(f"SQL generation failed: {exc}")
        
        return state

    def _generate_sql_from_plan(self, plan: dict, limit: int) -> str:
        """
        Generates SQL string from the plan dictionary.

        Args:
            plan: The execution plan.
            limit: The row limit to enforce.

        Returns:
            The generated SQL string.
        """
        tables = plan.get("tables", [])
        if not tables:
            raise ValueError("No tables in plan.")
        
        query = exp.select()
        query = self._build_select(query, plan)
        query = self._build_from(query, tables[0])
        query = self._build_joins(query, plan, tables)
        query = self._build_where(query, plan)
        query = self._build_group_by(query, plan)
        query = self._build_having(query, plan)
        query = self._build_order_by(query, plan)
        query = query.limit(limit)

        dialect_map = {
            "postgresql": "postgres",
            "mssql": "tsql",
            "mysql": "mysql",
            "sqlite": "sqlite",
            "oracle": "oracle",
        }
        target_dialect = dialect_map.get(self.profile_engine, self.profile_engine)
        
        final_sql = query.sql(dialect=target_dialect)
        return final_sql

    def _to_col(self, col_ref: dict) -> exp.Column | exp.Expression:
        """
        Converts a ColumnRef dictionary to a sqlglot expression.

        Args:
            col_ref: The column reference dictionary.

        Returns:
            A sqlglot Column or Expression object.
        """
        expr_str = col_ref["expr"]
        expr = sqlglot.parse_one(expr_str)
        
  
        return expr

    def _build_select(self, query: exp.Select, plan: dict) -> exp.Select:
        """Builds the SELECT clause."""
        select_cols = plan.get("select_columns", [])
        
        if not select_cols:
            pass

        for col in select_cols:
            expr = self._to_col(col)
            if col.get("alias"):
                 expr = exp.Alias(this=expr, alias=exp.Identifier(this=col["alias"], quoted=False))
            query = query.select(expr)
        
        return query

    def _build_from(self, query: exp.Select, main_table: dict) -> exp.Select:
        """Builds the FROM clause."""
        main_tbl_exp = sqlglot.to_table(main_table["name"])
        if main_table.get("alias"):
            main_tbl_exp.set("alias", exp.TableAlias(this=exp.Identifier(this=main_table["alias"], quoted=False)))
        return query.from_(main_tbl_exp)

    def _build_joins(self, query: exp.Select, plan: dict, tables: list) -> exp.Select:
        """Builds the JOIN clauses."""
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
        """Builds the WHERE clause."""
        filters = plan.get("filters", [])
        if filters:
            where_cond = None
            for flt in filters:
                col = self._to_col(flt["column"])
                val = flt["value"]
                op = flt["op"].upper()
                
                if isinstance(val, str):
                    val_exp = exp.Literal.string(val)
                elif isinstance(val, (int, float)):
                    val_exp = exp.Literal.number(val)
                elif isinstance(val, bool):
                    val_exp = exp.Boolean(this=val)
                else:
                    val_exp = exp.Literal.string(str(val))

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
        """Builds the GROUP BY clause."""
        for gb in plan.get("group_by", []):
            if isinstance(gb, dict):
                expr = gb["expr"]
            else:
                expr = gb
            query = query.group_by(sqlglot.parse_one(expr))
        return query

    def _build_having(self, query: exp.Select, plan: dict) -> exp.Select:
        """Builds the HAVING clause."""
        having = plan.get("having", [])
        if having:
            having_cond = None
            for hav in having:
                col = sqlglot.parse_one(hav["expr"])
                val = hav["value"]
                op = hav["op"].upper()
                
                if isinstance(val, str):
                    val_exp = exp.Literal.string(val)
                elif isinstance(val, (int, float)):
                    val_exp = exp.Literal.number(val)
                elif isinstance(val, bool):
                    val_exp = exp.Boolean(this=val)
                else:
                    val_exp = exp.Literal.string(str(val))

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
        """Builds the ORDER BY clause."""
        for ob in plan.get("order_by", []):
            query = query.order_by(self._to_col(ob["column"]), desc=(ob["direction"].lower() == "desc"))
        return query
