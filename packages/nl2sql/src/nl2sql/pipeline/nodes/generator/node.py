from __future__ import annotations

import sqlglot
from sqlglot import expressions as exp
from sqlglot.dialects.dialect import Dialect
from typing import Dict, Any, List, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.datasources import DatasourceRegistry
from nl2sql.common.logger import get_logger
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel, Expr, TableRef
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.context import NL2SQLContext

logger = get_logger("generator")


def ordered(expression: exp.Expression, direction: str) -> exp.Ordered:
    """Wraps an ORDER BY term the way sqlglot's own parser would.

    ``Select.order_by()`` only wraps its argument in ``exp.Ordered`` when it has
    to *parse* it; an ``Expression`` is taken as-is and the ``desc`` keyword is
    dropped. Every term this pipeline emits is already an ``Expression``, so
    both call sites must build the ``Ordered`` node themselves. Leaving it off
    silently loses the sort direction in the generator, and puts a bare
    ``exp.Anonymous`` into ``Order.expressions`` in the validator, where
    ``qualify()``'s positional-reference expansion reads its string ``this`` as
    an expression and raises.
    """
    return exp.Ordered(this=expression, desc=(direction == "desc"))


_BINARY_NODES = {
    "=": exp.EQ,
    "!=": exp.NEQ,
    ">": exp.GT,
    "<": exp.LT,
    ">=": exp.GTE,
    "<=": exp.LTE,
    "LIKE": exp.Like,
    "IS": exp.Is,
    "+": exp.Add,
    "-": exp.Sub,
    "*": exp.Mul,
    "/": exp.Div,
    "%": exp.Mod,
}


def _grouped(operand: exp.Expression) -> exp.Expression:
    """Parenthesises an operand that is itself an operator.

    sqlglot's generator prints a hand-built tree as-is and never adds the
    parentheses its parser would have seen: ``Mul(Add(a, b), c)`` prints as
    ``a + b * c``. The plan's tree already encodes the grouping, so any nested
    operator is wrapped to keep it.
    """
    if isinstance(operand, exp.Binary):
        return exp.Paren(this=operand)
    return operand


_MIRRORED_JOIN = {"left": "right", "right": "left"}


def _table(ref: TableRef) -> exp.Table:
    """Builds a qualified, aliased table reference for the FROM clause."""
    tbl = exp.Table(this=exp.Identifier(this=ref.name, quoted=False))
    if ref.schema_name:
        tbl.set("db", exp.Identifier(this=ref.schema_name, quoted=False))
    if ref.database:
        tbl.set("catalog", exp.Identifier(this=ref.database, quoted=False))
    if ref.alias:
        tbl.set("alias", exp.TableAlias(this=exp.Identifier(this=ref.alias, quoted=False)))
    return tbl


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
        """Converts a binary operation expression to sqlglot.

        Every operator is built as the sqlglot node its parser would produce.
        An operator with no mapping is an error: rendering it as a function
        named after the operator (``*(a, b)``) is never valid SQL.
        """
        if not expr.left or not expr.right:
            raise ValueError("Binary expression missing operands")

        left = self.visit(expr.left)
        right = self.visit(expr.right)
        op = str(expr.op).upper()

        if op == "AND":
            if isinstance(left, exp.Or): left = exp.Paren(this=left)
            if isinstance(right, exp.Or): right = exp.Paren(this=right)
            return exp.And(this=left, expression=right)
        if op == "OR": return exp.Or(this=left, expression=right)
        if op == "IN":
            values = right.expressions if isinstance(right, exp.Tuple) else [right]
            return exp.In(this=_grouped(left), expressions=values)
        if op == "IS NOT":
            return exp.Not(this=exp.Is(this=_grouped(left), expression=_grouped(right)))

        node = _BINARY_NODES.get(op)
        if node is None:
            raise ValueError(f"Unsupported binary operator: {expr.op}")
        return node(this=_grouped(left), expression=_grouped(right))

    def _visit_unary(self, expr: Expr) -> exp.Expression:
        """Converts a unary operation expression to sqlglot."""
        target = expr.expr
        if not target:
            raise ValueError("Unary expression missing target")

        node = _grouped(self.visit(target))
        op = str(expr.op).upper()

        if op == "NOT":
            return exp.Not(this=node)
        if op == "-":
            return exp.Neg(this=node)

        return exp.Paren(this=node)

    def _visit_case(self, expr: Expr) -> exp.Case:
        """Converts a CASE expression to sqlglot.

        A CASE branch is ``exp.If``, as sqlglot's parser builds it.
        ``exp.When`` is MERGE's WHEN clause: used here it printed every branch
        as ``THEN`` with nothing after it.
        """
        when_list = []

        if expr.whens:
            for w in sorted(expr.whens, key=lambda x: x.ordinal):
                when_list.append(
                    exp.If(
                        this=self.visit(w.condition),
                        true=self.visit(w.result)
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

    def __call__(self, state: SubgraphExecutionState) -> Dict[str, Any]:
        """Executes the generator node.

        Converts the PlanModel into a SQL string tailored for the target
        datasource's dialect.

        Args:
            state (SubgraphExecutionState): The current state containing the execution plan.

        Returns:
            Dict[str, Any]: A dictionary with the generated 'sql_draft' and reasoning.
        """
        try:
            datasource_id = state.sub_query.datasource_id if state.sub_query else None
            if not datasource_id:
                raise ValueError("No datasource selected")
            plan = state.ast_planner_response.plan if state.ast_planner_response else None
            if not plan:
                raise ValueError("No plan provided")

            adapter = self.ds_registry.get_adapter(datasource_id)
            dialect = adapter.get_dialect()

            row_limit = adapter.row_limit or 1000
            limit = min(int(plan.limit or row_limit), row_limit)

            sql = self._generate_sql(plan, limit, dialect)

            response = GeneratorResponse(
                sql_draft=sql,
                reasoning=[{"node": self.node_name, "content": ["Generated SQL", sql]}],
            )
            return {
                "generator_response": response,
                "reasoning": response.reasoning,
            }

        except Exception as exc:
            logger.exception(exc)
            error = PipelineError(
                node=self.node_name,
                message=str(exc),
                error_code=ErrorCode.SQL_GEN_FAILED,
                severity=ErrorSeverity.ERROR,
                stack_trace=str(exc),
            )
            return {
                "generator_response": GeneratorResponse(errors=[error]),
                "errors": [error],
            }

    def _attach_joins(
        self,
        query: exp.Select,
        plan: PlanModel,
        declared: Dict[str, Any],
        primary_alias: str,
        visitor: SqlVisitor,
    ) -> exp.Select:
        """Adds every joined table to the FROM clause exactly once.

        ``left_alias``/``right_alias`` name the two sides of a join; they do not
        say which one is new. Each join attaches whichever side is not yet in
        scope, and joins are taken in dependency order -- the lowest-ordinal
        join that touches the tables already in scope goes next -- so a plan
        may list its joins in any order. When the new table is the join's left
        side an outer join is mirrored, so the table the plan preserves is
        still the one preserved.

        Raises:
            ValueError: If a join names an undeclared alias, joins two tables
                that are already in scope, or cannot be reached from the FROM
                table; or if a declared table is never joined. Each of these
                is a malformed plan, and guessing would produce wrong SQL.
        """
        pending = sorted(plan.joins, key=lambda x: x.ordinal)
        for j in pending:
            for alias in (j.left_alias, j.right_alias):
                if alias not in declared:
                    raise ValueError(f"Join references unknown alias {alias}")

        in_scope = {primary_alias}
        while pending:
            j = next(
                (j for j in pending if j.left_alias in in_scope or j.right_alias in in_scope),
                None,
            )
            if j is None:
                stranded = ", ".join(f"{p.left_alias}-{p.right_alias}" for p in pending)
                raise ValueError(
                    f"Join(s) {stranded} do not connect to the FROM table "
                    f"'{primary_alias}' or to any table joined to it."
                )
            if j.left_alias in in_scope and j.right_alias in in_scope:
                raise ValueError(
                    f"Join {j.left_alias}-{j.right_alias} joins two tables that are "
                    "already in the query; each table must be joined exactly once."
                )

            if j.right_alias in in_scope:
                new_alias = j.left_alias
                join_type = _MIRRORED_JOIN.get(j.join_type, j.join_type)
            else:
                new_alias = j.right_alias
                join_type = j.join_type

            query = query.join(
                _table(declared[new_alias]),
                on=visitor.visit(j.condition),
                join_type=join_type,
            )
            in_scope.add(new_alias)
            pending.remove(j)

        unjoined = [alias for alias in declared if alias not in in_scope]
        if unjoined:
            raise ValueError(
                f"Table alias(es) {', '.join(unjoined)} are declared in the plan "
                "but never joined to the FROM table."
            )
        return query

    def _generate_sql(self, plan: PlanModel, limit: int, dialect: str) -> str:
        """Internal helper to build and optimize the SQL query."""
        visitor = SqlVisitor()
        query = exp.select()
        selected = []

        for s in sorted(plan.select_items, key=lambda x: x.ordinal):
            e = visitor.visit(s.expr)
            selected.append((e, s.alias))
            if s.alias:
                e = exp.Alias(this=e, alias=exp.Identifier(this=s.alias, quoted=False))
            query = query.select(e)

        tables = sorted(plan.tables, key=lambda x: x.ordinal)
        if not tables:
            raise ValueError("Plan has no tables")

        declared = {t.alias: t for t in tables}
        primary = tables[0]
        query = query.from_(_table(primary))
        query = self._attach_joins(query, plan, declared, primary.alias, visitor)

        if plan.where:
            query = query.where(visitor.visit(plan.where))

        for g in sorted(plan.group_by, key=lambda x: x.ordinal):
            query = query.group_by(visitor.visit(g.expr))

        if plan.having:
            query = query.having(visitor.visit(plan.having))

        ordered_on = set()
        for o in sorted(plan.order_by, key=lambda x: x.ordinal):
            term = visitor.visit(o.expr)
            ordered_on.add(term.sql())
            query = query.order_by(ordered(term, o.direction))

        # Tie-breakers: without them LIMIT can keep a different subset of rows
        # on each run. Ordering by every selected column never changes what the
        # query means. Aliased items are named by alias, never by position, and
        # constants are skipped (they order nothing, and ``ORDER BY 1`` is a
        # position). Each keeps the dialect's own NULL placement, so no
        # ``NULLS LAST`` (or a CASE emulating it) is rendered.
        nulls_first = Dialect.get_or_raise(dialect).NULL_ORDERING == "nulls_are_small"
        for e, alias in selected:
            if not e.find(exp.Column) or (isinstance(e, exp.Column) and e.name == "*"):
                continue
            key = exp.Column(this=exp.Identifier(this=alias, quoted=False)) if alias else e
            if e.sql() in ordered_on or key.sql() in ordered_on:
                continue
            ordered_on.add(key.sql())
            query = query.order_by(exp.Ordered(this=key.copy(), nulls_first=nulls_first))

        query = query.limit(limit)

        return query.sql(dialect=dialect)
