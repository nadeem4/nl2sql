from __future__ import annotations

import sqlglot
from sqlglot import expressions as exp
from typing import Dict, Any, List, cast

from nl2sql.pipeline.state import GraphState
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.datasources import DatasourceRegistry
from nl2sql.common.logger import get_logger
from nl2sql.pipeline.nodes.planner.schemas import PlanModel, Expr

logger = get_logger("generator")

class SqlVisitor:
    """
    Compiler that transforms a strictly typed Expr AST into a sqlglot Expression tree.
    Does NOT perform string parsing.
    """
    
    def visit(self, expr: Expr) -> exp.Expression:
        if expr.kind == "literal":
            return self._visit_literal(expr)
        elif expr.kind == "column":
            return self._visit_column(expr)
        elif expr.kind == "func":
            return self._visit_func(expr)
        elif expr.kind == "binary":
            return self._visit_binary(expr)
        elif expr.kind == "unary":
            return self._visit_unary(expr)
        elif expr.kind == "case":
            return self._visit_case(expr)
        raise ValueError(f"Unknown validation kind: {expr.kind}")

    def _visit_literal(self, expr: Expr) -> exp.Expression:
        val = expr.value
        if val is None:
            return exp.Null()
        if isinstance(val, bool):
            return exp.Boolean(this=val)
        if isinstance(val, (int, float)):
            return exp.Literal.number(val)
        return exp.Literal.string(str(val))

    def _visit_column(self, expr: Expr) -> exp.Column:
        ident = exp.Identifier(this=expr.column_name, quoted=False)
        if expr.alias:
            return exp.Column(this=ident, table=exp.Identifier(this=expr.alias, quoted=False))
        return exp.Column(this=ident)

    def _visit_func(self, expr: Expr) -> exp.Expression:
        if str(expr.func_name).upper() in ("TUPLE", "LIST"):
            return exp.Tuple(expressions=[self.visit(arg) for arg in expr.args])
            
        return exp.Anonymous(
            this=expr.func_name,
            expressions=[self.visit(arg) for arg in expr.args]
        )

    def _visit_binary(self, expr: Expr) -> exp.Expression:
        if not expr.left or not expr.right:
             raise ValueError("Binary expression missing left/right operands")
             
        left = self.visit(expr.left)
        right = self.visit(expr.right)
        op = str(expr.op).upper()

        if op == "=": return exp.EQ(this=left, expression=right)
        if op == "!=": return exp.NEQ(this=left, expression=right)
        if op == ">": return exp.GT(this=left, expression=right)
        if op == "<": return exp.LT(this=left, expression=right)
        if op == ">=": return exp.GTE(this=left, expression=right)
        if op == "<=": return exp.LTE(this=left, expression=right)
        if op == "AND": return exp.And(this=left, expression=right)
        if op == "OR": return exp.Paren(this=exp.Or(this=left, expression=right))
        if op == "LIKE": return exp.Like(this=left, expression=right)
        if op == "IN": 
            if isinstance(right, exp.Tuple):
                return exp.In(this=left, expressions=right.expressions)
            return exp.In(this=left, expressions=[right])
            
        return exp.Anonymous(this=op, expressions=[left, right])

    def _visit_unary(self, expr: Expr) -> exp.Expression:
        # Some unary ops might use 'expr' field
        target = expr.expr or expr.left 
        if not target:
             raise ValueError("Unary expression missing target expr")
             
        e = self.visit(target)
        op = str(expr.op).upper()
        if op == "NOT": return exp.Not(this=e)
        if op == "-": return exp.Neg(this=e)
        return exp.Paren(this=e) 

    def _visit_case(self, expr: Expr) -> exp.Case:
        cases = []
        if expr.whens:
            for when_item in expr.whens:
                cases.append(exp.When(this=self.visit(when_item.operand), then=self.visit(when_item.result)))
        
        default = self.visit(expr.else_expr) if expr.else_expr else None
        return exp.Case(ifs=cases, default=default)


class GeneratorNode:
    def __init__(self, registry: DatasourceRegistry):
        self.registry = registry

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        node_name = "generator"
        try:
            if not state.selected_datasource_id:
                raise ValueError("No datasource_id present")
            if not state.plan:
                raise ValueError("No plan provided")

            plan_model = state.plan
            
            # Needed for row_limit
            profile = self.registry.get_profile(state.selected_datasource_id)
            dialect = self.registry.get_dialect(state.selected_datasource_id)
            
            row_limit = profile.row_limit if profile else 1000
            limit = min(int(plan_model.limit or row_limit), row_limit)

            sql = self._generate_sql(plan_model, limit, dialect)

            return {
                "sql_draft": sql,
                "reasoning": [{"node": node_name, "content": ["Generated SQL", sql]}]
            }

        except Exception as exc:
            logger.exception(exc)
            return {
                "errors": [PipelineError(node=node_name, message=str(exc), error_code=ErrorCode.SQL_GEN_FAILED, severity=ErrorSeverity.ERROR, stack_trace=str(exc))]
            }

    def _generate_sql(self, plan: PlanModel, limit: int, dialect: str) -> str:
        visitor = SqlVisitor()
        query = exp.select()

        for item in sorted(plan.select_items, key=lambda x: x.ordinal):
            expression = visitor.visit(item.expr)
            if item.alias:
                expression = exp.Alias(this=expression, alias=exp.Identifier(this=item.alias, quoted=False))
            query = query.select(expression)

        sorted_tables = sorted(plan.tables, key=lambda x: x.ordinal)
        if not sorted_tables:
            raise ValueError("Plan must have at least one table")
        
        primary = sorted_tables[0]
        tbl = exp.Table(this=exp.Identifier(this=primary.name, quoted=False))
        if primary.schema_name:
            tbl.set("db", exp.Identifier(this=primary.schema_name, quoted=False))
        if primary.database:
            tbl.set("catalog", exp.Identifier(this=primary.database, quoted=False))
             
        if primary.alias:
            tbl.set("alias", exp.TableAlias(this=exp.Identifier(this=primary.alias, quoted=False)))
        query = query.from_(tbl)

        alias_to_table_name = {t.alias: t.name for t in sorted_tables}
        
        for j in sorted(plan.joins, key=lambda x: x.ordinal):
            table_name = alias_to_table_name.get(j.right_alias)
            if not table_name:
                raise ValueError(f"Join refers to unknown alias '{j.right_alias}'")

            right_tbl = exp.Table(this=exp.Identifier(this=table_name, quoted=False))
            
            right_tbl.set("alias", exp.TableAlias(this=exp.Identifier(this=j.right_alias, quoted=False)))
            
            condition = visitor.visit(j.condition)
            query = query.join(right_tbl, on=condition, kind=j.join_type)

        if plan.where:
            query = query.where(visitor.visit(plan.where))

        for g in sorted(plan.group_by, key=lambda x: x.ordinal):
            query = query.group_by(visitor.visit(g.expr))

        if plan.having:
            query = query.having(visitor.visit(plan.having))

        for o in sorted(plan.order_by, key=lambda x: x.ordinal):
            query = query.order_by(visitor.visit(o.expr), desc=(o.direction == "desc"))

        query = query.limit(limit)

        return query.sql(dialect=dialect)
