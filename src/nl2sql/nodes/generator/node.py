from __future__ import annotations

import sqlglot
from sqlglot import expressions as exp
from typing import Dict, Any

from nl2sql.schemas import GraphState
from nl2sql.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.logger import get_logger

logger = get_logger("generator")


class GeneratorNode:
    def __init__(self, registry: DatasourceRegistry):
        self.registry = registry

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        node_name = "generator"

        try:
            if not state.selected_datasource_id:
                return {
                    "errors": [
                        PipelineError(
                            node=node_name,
                            message="No datasource_id present before SQL generation.",
                            severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.MISSING_DATASOURCE_ID,
                        )
                    ]
                }

            if not state.plan:
                return {
                    "errors": [
                        PipelineError(
                            node=node_name,
                            message="No plan provided for SQL generation.",
                            severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.MISSING_PLAN,
                        )
                    ]
                }

            profile = self.registry.get_profile(state.selected_datasource_id)
            self.profile_engine = profile.engine
            self.row_limit = profile.row_limit

            limit = min(
                int(state.plan.get("limit", self.row_limit)),
                self.row_limit,
            )

            sql = self._generate_sql_from_plan(state.plan, limit)

            return {
                "sql_draft": sql,
                "reasoning": [
                    {
                        "node": node_name,
                        "content": [
                            f"Generated SQL for datasource={state.selected_datasource_id}",
                            sql,
                        ],
                    }
                ],
            }

        except Exception as exc:
            logger.exception(exc)
            return {
                "sql_draft": None,
                "errors": [
                    PipelineError(
                        node=node_name,
                        message=f"SQL generation failed: {exc}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.SQL_GEN_FAILED,
                        stack_trace=str(exc),
                    )
                ],
            }

    def _generate_sql_from_plan(self, plan: dict, limit: int) -> str:
        query = exp.select()
        query = self._build_select(query, plan)
        query = self._build_from(query, plan["tables"][0])
        query = self._build_joins(query, plan)
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

        return query.sql(dialect=dialect_map.get(self.profile_engine, self.profile_engine))

    def _to_expr(self, expr_str: str) -> exp.Expression:
        return sqlglot.parse_one(expr_str)

    def _build_select(self, query: exp.Select, plan: dict) -> exp.Select:
        for col in plan.get("select_columns", []):
            expr = self._to_expr(col["expr"])
            if col.get("alias"):
                expr = exp.Alias(this=expr, alias=exp.Identifier(this=col["alias"]))
            query = query.select(expr)
        return query

    def _build_from(self, query: exp.Select, table: dict) -> exp.Select:
        tbl = exp.Table(this=exp.Identifier(this=table["name"]))
        if table.get("alias"):
            tbl.set("alias", exp.TableAlias(this=exp.Identifier(this=table["alias"])))
        return query.from_(tbl)

    def _build_joins(self, query: exp.Select, plan: dict) -> exp.Select:
        table_map = {t["name"]: t.get("alias") for t in plan.get("tables", [])}

        for j in plan.get("joins", []):
            tbl = exp.Table(this=exp.Identifier(this=j["right"]))
            alias = table_map.get(j["right"])
            if alias:
                tbl.set("alias", exp.TableAlias(this=exp.Identifier(this=alias)))

            on_cond = None
            for cond in j.get("on", []):
                parsed = self._to_expr(cond)
                on_cond = parsed if on_cond is None else exp.And(this=on_cond, expression=parsed)

            query = query.join(tbl, on=on_cond, kind=j.get("join_type", "inner"))
        return query

    def _build_where(self, query: exp.Select, plan: dict) -> exp.Select:
        cond = None

        for flt in plan.get("filters", []):
            left = self._to_expr(flt["column"]["expr"])
            op = flt["op"].upper()
            value = flt["value"]

            right = self._literal(value)

            if op == "=":
                expr = exp.EQ(this=left, expression=right)
            elif op in ("!=", "<>"):
                expr = exp.NEQ(this=left, expression=right)
            elif op == ">":
                expr = exp.GT(this=left, expression=right)
            elif op == "<":
                expr = exp.LT(this=left, expression=right)
            elif op == ">=":
                expr = exp.GTE(this=left, expression=right)
            elif op == "<=":
                expr = exp.LTE(this=left, expression=right)
            elif op == "BETWEEN":
                low, high = map(str.strip, str(value).split("AND"))
                expr = exp.Between(
                    this=left,
                    low=self._literal(low),
                    high=self._literal(high),
                )
            elif op == "IN":
                items = [self._literal(v) for v in value]
                expr = exp.In(this=left, expressions=items)
            else:
                expr = self._to_expr(f"{flt['column']['expr']} {op} {value}")

            if cond is None:
                cond = expr
            else:
                logic = flt.get("logic", "and").lower()
                cond = exp.Or(this=cond, expression=expr) if logic == "or" else exp.And(this=cond, expression=expr)

        return query.where(cond) if cond else query

    def _build_group_by(self, query: exp.Select, plan: dict) -> exp.Select:
        for g in plan.get("group_by", []):
            query = query.group_by(self._to_expr(g["expr"]))
        return query

    def _build_having(self, query: exp.Select, plan: dict) -> exp.Select:
        cond = None
        for h in plan.get("having", []):
            left = self._to_expr(h["expr"])
            right = self._literal(h["value"])
            op = h["op"]

            expr = self._to_expr(f"{h['expr']} {op} {h['value']}")

            cond = expr if cond is None else exp.And(this=cond, expression=expr)
        return query.having(cond) if cond else query

    def _build_order_by(self, query: exp.Select, plan: dict) -> exp.Select:
        for o in plan.get("order_by", []):
            query = query.order_by(
                self._to_expr(o["column"]["expr"]),
                desc=o["direction"].lower() == "desc",
            )
        return query

    def _literal(self, value):
        if isinstance(value, bool):
            return exp.Boolean(this=value)
        if isinstance(value, (int, float)):
            return exp.Literal.number(value)
        return exp.Literal.string(str(value).strip("'").strip('"'))
