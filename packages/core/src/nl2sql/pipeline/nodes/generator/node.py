from __future__ import annotations

import sqlglot
from sqlglot import expressions as exp
from typing import Dict, Any, List, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.datasources import DatasourceRegistry
from nl2sql.common.logger import get_logger
from nl2sql.pipeline.nodes.planner.schemas import PlanModel, Expr
from nl2sql.context import NL2SQLContext

logger = get_logger("generator")


class SqlVisitor:
    """Visits the PlanModel AST and converts it to sqlglot expressions.

    This visitor traverses the deterministic AST (Expr) produced by the Planner
    and builds a corresponding sqlglot expression tree, which can then be
    transpiled to the target dialect.
    """

    def visit(self, expr: Expr) -> exp.Expression:
        """Dispatches the visit to the appropriate method based on expression kind.

        Args:
            expr (Expr): The expression node to visit.

        Returns:
            exp.Expression: The corresponding sqlglot expression.

        Raises:
            ValueError: If the expression kind is unknown.
        """
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
        raise ValueError(f"Unknown expression kind: {expr.kind}")

    def _visit_literal(self, expr: Expr) -> exp.Expression:
        """Converts a literal expression to sqlglot."""
        val = expr.value
        if val is None:
            return exp.Null()
        if isinstance(val, bool):
            return exp.Boolean(this="TRUE" if val else "FALSE")
        if isinstance(val, (int, float)):
            return exp.Literal.number(str(val))
        return exp.Literal.string(str(val))

    def _visit_column(self, expr: Expr) -> exp.Column:
        """Converts a column expression to sqlglot."""
        ident = exp.Identifier(this=expr.column_name, quoted=False)
        if expr.alias:
            return exp.Column(this=ident, table=exp.Identifier(this=expr.alias, quoted=False))
        return exp.Column(this=ident)

    def _visit_func(self, expr: Expr) -> exp.Expression:
        """Converts a function call expression to sqlglot."""
        if str(expr.func_name).upper() in ("TUPLE", "LIST"):
            return exp.Tuple(expressions=[self.visit(arg) for arg in expr.args])

        return exp.Anonymous(
            this=expr.func_name,
            expressions=[self.visit(arg) for arg in expr.args]
        )

    def _visit_binary(self, expr: Expr) -> exp.Expression:
        """Converts a binary operation expression to sqlglot."""
        if not expr.left or not expr.right:
            raise ValueError("Binary expression missing operands")

        left = self.visit(expr.left)
        right = self.visit(expr.right)
        op = str(expr.op).upper()

        if op == "=": return exp.EQ(this=left, expression=right)
        if op == "!=": return exp.NEQ(this=left, expression=right)
        if op == ">": return exp.GT(this=left, expression=right)
        if op == "<": return exp.LT(this=left, expression=right)
        if op == ">=": return exp.GTE(this=left, expression=right)
        if op == "<=": return exp.LTE(this=left, expression=right)
        if op == "AND":
            if isinstance(left, exp.Or): left = exp.Paren(this=left)
            if isinstance(right, exp.Or): right = exp.Paren(this=right)
            return exp.And(this=left, expression=right)
        if op == "OR": return exp.Or(this=left, expression=right)
        if op == "LIKE": return exp.Like(this=left, expression=right)
        if op == "IN":
            values = right.expressions if isinstance(right, exp.Tuple) else [right]
            return exp.In(this=left, expressions=values)

        return exp.Anonymous(this=op, expressions=[left, right])

    def _visit_unary(self, expr: Expr) -> exp.Expression:
        """Converts a unary operation expression to sqlglot."""
        target = expr.expr
        if not target:
            raise ValueError("Unary expression missing target")

        node = self.visit(target)
        op = str(expr.op).upper()

        if op == "NOT":
            return exp.Not(this=node)
        if op == "-":
            return exp.Neg(this=node)

        return exp.Paren(this=node)

    def _visit_case(self, expr: Expr) -> exp.Case:
        """Converts a CASE expression to sqlglot."""
        when_list = []

        if expr.whens:
            for w in expr.whens:
                when_list.append(
                    exp.When(
                        this=self.visit(w.condition),
                        then=self.visit(w.result)
                    )
                )

        default = self.visit(expr.else_expr) if expr.else_expr else None
        return exp.Case(ifs=when_list, default=default)


class GeneratorNode:
    """Generates the final SQL string from the PlanModel using sqlglot.

    Attributes:
        registry (DatasourceRegistry): The registry to fetch datasource dialects.
    """

    def __init__(self, ctx: NL2SQLContext):
        """Initializes the GeneratorNode.

        Args:
            registry (DatasourceRegistry): The registry of datasources.
        """
        self.node_name = self.__class__.__name__.lower().replace('node', '')
        self.ds_registry = ctx.ds_registry

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """Executes the generator node.

        Converts the PlanModel into a SQL string tailored for the target
        datasource's dialect.

        Args:
            state (GraphState): The current state containing the execution plan.

        Returns:
            Dict[str, Any]: A dictionary with the generated 'sql_draft' and reasoning.
        """
        try:
            if not state.selected_datasource_id:
                raise ValueError("No datasource selected")
            if not state.plan:
                raise ValueError("No plan provided")

            plan: PlanModel = state.plan
            
            # Fix: Use adapter directly since get_profile is removed
            adapter = self.ds_registry.get_adapter(state.selected_datasource_id)
            dialect = adapter.get_dialect()

            row_limit = adapter.row_limit or 1000
            limit = min(int(plan.limit or row_limit), row_limit)

            sql = self._generate_sql(plan, limit, dialect)

            return {
                "sql_draft": sql,
                "reasoning": [
                    {"node": self.node_name, "content": ["Generated SQL", sql]}
                ]
            }

        except Exception as exc:
            logger.exception(exc)
            return {
                "errors": [
                    PipelineError(
                        node=self.node_name,
                        message=str(exc),
                        error_code=ErrorCode.SQL_GEN_FAILED,
                        severity=ErrorSeverity.ERROR,
                        stack_trace=str(exc),
                    )
                ]
            }

    def _generate_sql(self, plan: PlanModel, limit: int, dialect: str) -> str:
        """Internal helper to build and optimize the SQL query."""
        visitor = SqlVisitor()
        query = exp.select()

        for s in sorted(plan.select_items, key=lambda x: x.ordinal):
            e = visitor.visit(s.expr)
            if s.alias:
                e = exp.Alias(this=e, alias=exp.Identifier(this=s.alias, quoted=False))
            query = query.select(e)

        tables = sorted(plan.tables, key=lambda x: x.ordinal)
        if not tables:
            raise ValueError("Plan has no tables")

        primary = tables[0]
        tbl = exp.Table(this=exp.Identifier(this=primary.name, quoted=False))

        if primary.schema_name:
            tbl.set("db", exp.Identifier(this=primary.schema_name, quoted=False))
        if primary.database:
            tbl.set("catalog", exp.Identifier(this=primary.database, quoted=False))
        if primary.alias:
            tbl.set("alias", exp.TableAlias(this=exp.Identifier(this=primary.alias, quoted=False)))

        query = query.from_(tbl)

        alias_map = {t.alias: t.name for t in tables}

        for j in sorted(plan.joins, key=lambda x: x.ordinal):
            name = alias_map.get(j.right_alias)
            if not name:
                raise ValueError(f"Join references unknown alias {j.right_alias}")

            right = exp.Table(this=exp.Identifier(this=name, quoted=False))
            right.set("alias", exp.TableAlias(this=exp.Identifier(this=j.right_alias, quoted=False)))

            condition = visitor.visit(j.condition)

            query = query.join(
                right,
                on=condition,
                join_type=j.join_type
            )

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
