from __future__ import annotations

from typing import Set, Dict, Any, List, Optional, Tuple, TYPE_CHECKING
import traceback

from sqlglot import expressions as exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify

if TYPE_CHECKING:
    from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.pipeline.nodes.ast_planner.functions import (
    ALLOWED_FUNCTIONS,
    DATE_UNITS,
    date_operation,
    is_allowed_function,
)
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel, Expr
from nl2sql.pipeline.nodes.generator.node import SqlVisitor, ordered
from nl2sql.context import NL2SQLContext
from nl2sql.auth.rbac import table_allowed
from nl2sql.common.logger import get_logger
from nl2sql.common.settings import settings
from nl2sql.pipeline.nodes.validator.schemas import LogicalValidatorResponse, ValidationCheck


logger = get_logger("logical_validator")

_MAX_HINTED_COLUMNS = 15

# What a caller is told when the plan needs a table their role cannot read.
# It names nothing, so it reveals nothing about what exists; the table and
# role go to the log and, through the error's details, the run trace.
REFUSAL_MESSAGE = "You do not have permission to see the data this question requires."


def _plan_exprs(plan: PlanModel):
    """Every expression node in the plan, nested ones included."""
    def walk(node: Optional[Expr]):
        if not node:
            return
        yield node
        for child in (*node.args, node.left, node.right, node.expr, node.else_expr):
            yield from walk(child)
        for when in node.whens:
            yield from walk(when.condition)
            yield from walk(when.result)

    roots = [s.expr for s in plan.select_items] + [g.expr for g in plan.group_by]
    roots += [o.expr for o in plan.order_by] + [j.condition for j in plan.joins]
    roots += [plan.where, plan.having]
    for root in roots:
        yield from walk(root)


class ValidationSqlVisitor(SqlVisitor):
    """``SqlVisitor`` variant used to build a throw-away tree for validation.

    Identical to the generator's visitor except that a ``*`` column name is
    rendered as a real sqlglot star, so the optimizer expands it instead of
    trying to resolve a column literally named ``*``.
    """

    def _visit_column(self, expr: Expr) -> exp.Expression:
        """Converts a column expression, mapping wildcards to sqlglot stars."""
        if (expr.column_name or "").strip() == "*":
            if expr.alias:
                return exp.Column(
                    this=exp.Star(),
                    table=exp.Identifier(this=expr.alias, quoted=False),
                )
            return exp.Star()
        return super()._visit_column(expr)


class LogicalValidatorNode:
    """Validates the generated AST (PlanModel).

    Performs static validation on the AST structure and user policies.

    Attributes:
        registry (DatasourceRegistry): Registry to fetch schemas and profiles.
    """

    def __init__(self, ctx: NL2SQLContext):
        """Initializes the LogicalValidatorNode.

        Args:
            registry (DatasourceRegistry): The registry of datasources.
        """
        self.registry = ctx.ds_registry
        self.rbac = ctx.rbac
        self.strict_columns = settings.logical_validator_strict_columns

    def _normalize_table_key(
        self,
        table_name: str,
        schema_name: Optional[str] = None,
        database: Optional[str] = None,
    ) -> str:
        parts = []
        if database:
            parts.append(database)
        if schema_name:
            parts.append(schema_name)
        parts.append(table_name)
        return ".".join(parts).lower()

    def _normalize_name(self, value: Optional[str]) -> str:
        """Reduces an identifier to its bare, comparable form.

        Both sides of the relationship check must arrive here in the same
        shape. Plan tables carry bare names (``Album``) while the schema
        retriever emits relationships keyed by ``TableRef.full_name``, which is
        delimited and qualified (``[main].[Album]``). Stripping the delimiters
        is a de-quoting step, not a relaxation: ``[album]`` and ``album`` are
        the same identifier, and unrelated identifiers still compare unequal.
        """
        if not value:
            return ""
        return value.lower().split(".")[-1].strip().strip('[]"`')

    def _build_allowed_schema(
        self, state: SubgraphExecutionState
    ) -> Tuple[Dict[str, Set[str]], List[Dict[str, Any]]]:
        table_to_cols: Dict[str, Set[str]] = {}
        relationships: List[Dict[str, Any]] = []

        for rt in state.relevant_tables:
            table_name = self._normalize_name(rt.name)
            if not table_name:
                continue
            cols: Set[str] = set()
            for c in rt.columns or []:
                col_name = self._normalize_name(c.name)
                if not col_name:
                    continue
                cols.add(col_name)
            table_to_cols[table_name] = cols

            for rel in getattr(rt, "relationships", []) or []:
                relationships.append(rel)

        return table_to_cols, relationships

    def _extract_join_pairs(self, expr: Expr) -> List[Tuple[str, str, str, str]]:
        pairs: List[Tuple[str, str, str, str]] = []

        def walk(node: Optional[Expr]) -> None:
            if not node:
                return
            if node.kind == "binary" and node.op == "=":
                left = node.left
                right = node.right
                if left and right and left.kind == "column" and right.kind == "column":
                    if left.alias and right.alias and left.column_name and right.column_name:
                        pairs.append(
                            (
                                left.alias,
                                self._normalize_name(left.column_name),
                                right.alias,
                                self._normalize_name(right.column_name),
                            )
                        )
            if node.kind == "binary":
                walk(node.left)
                walk(node.right)
            elif node.kind == "func":
                for arg in node.args:
                    walk(arg)
            elif node.kind == "unary":
                walk(node.expr)
            elif node.kind == "case":
                for when in node.whens:
                    walk(when.condition)
                    walk(when.result)
                walk(node.else_expr)

        walk(expr)
        return pairs

    def _condition_aliases(self, condition: Expr) -> Set[str]:
        """Returns the table aliases referenced by a join condition."""
        node = ValidationSqlVisitor().visit(condition)
        return {col.table for col in node.find_all(exp.Column) if col.table}

    def _resolve_plan_tables(
        self, state: SubgraphExecutionState, plan: PlanModel
    ) -> Tuple[Dict[str, Set[str]], Set[str], List[PipelineError]]:
        """Resolves every plan table against the retrieved schema.

        The resulting alias-to-column map is what is handed to sqlglot's
        ``qualify()`` as the schema. Table existence is still checked here
        because ``qualify()`` silently ignores relations that are absent from
        the schema it is given.

        Args:
            state (SubgraphExecutionState): Current execution state containing relevant_tables.
            plan (PlanModel): The plan containing table references.

        Returns:
            Tuple containing:
            - alias_to_cols (Dict[str, Set[str]]): Map of alias to column names.
            - plan_aliases (Set[str]): Set of aliases defined in the plan.
            - errors (List[PipelineError]): List of errors if tables are missing.
        """
        alias_to_cols: Dict[str, Set[str]] = {}
        plan_aliases: Set[str] = set()
        errors: List[PipelineError] = []

        simple_map: Dict[str, List[Any]] = {}
        full_map: Dict[str, Any] = {}
        for rt in state.relevant_tables:
            rt_name = (rt.name or "").lower()
            if not rt_name:
                continue
            simple_map.setdefault(rt_name.split(".")[-1], []).append(rt)
            if "." in rt_name:
                full_map[rt_name] = rt

        for t in plan.tables:
            plan_aliases.add(t.alias)

            found_table = None
            if t.schema_name or t.database:
                found_table = full_map.get(
                    self._normalize_table_key(t.name, t.schema_name, t.database)
                )

            if not found_table:
                candidates = simple_map.get((t.name or "").lower(), [])
                if len(candidates) > 1:
                    errors.append(
                        PipelineError(
                            node="logical_validator",
                            message=(
                                f"Ambiguous table '{t.name}' across schemas; "
                                "plan must specify schema."
                            ),
                            severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.TABLE_NOT_FOUND,
                        )
                    )
                    continue
                found_table = candidates[0] if candidates else None

            if not found_table:
                errors.append(
                    PipelineError(
                        node="logical_validator",
                        message=f"Table '{t.name}' not found in relevant tables.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.TABLE_NOT_FOUND
                    )
                )
                continue

            alias_to_cols[t.alias] = {
                self._normalize_name(c.name) for c in found_table.columns
            }

        logger.debug("Validator alias map: %s", alias_to_cols)
        return alias_to_cols, plan_aliases, errors

    def _build_validation_query(
        self, plan: PlanModel, aliases: List[str]
    ) -> exp.Select:
        """Builds a throw-away sqlglot query used purely for column resolution.

        Every resolved plan table becomes a relation named after its alias, so
        the sqlglot schema is keyed by alias and resolution scoping matches the
        plan exactly. Tables are cross-joined rather than joined on their
        conditions because the plan's alias-to-column visibility does not depend
        on join structure; join conditions are folded into the WHERE clause so
        that their columns are resolved too.
        """
        visitor = ValidationSqlVisitor()

        selects = [visitor.visit(s.expr) for s in plan.select_items] or [exp.Star()]
        query = exp.select(*selects)

        query = query.from_(exp.Table(this=exp.Identifier(this=aliases[0], quoted=False)))
        for alias in aliases[1:]:
            query = query.join(
                exp.Table(this=exp.Identifier(this=alias, quoted=False)),
                join_type="CROSS",
            )

        for j in plan.joins:
            query = query.where(visitor.visit(j.condition))
        if plan.where:
            query = query.where(visitor.visit(plan.where))
        for g in plan.group_by:
            query = query.group_by(visitor.visit(g.expr))
        if plan.having:
            query = query.having(visitor.visit(plan.having))
        for o in plan.order_by:
            query = query.order_by(ordered(visitor.visit(o.expr), o.direction))

        return query

    def _describe_column_failure(
        self, alias: str, column: str, alias_to_cols: Dict[str, Set[str]]
    ) -> str:
        """Turns an unresolvable column reference into actionable plan feedback.

        sqlglot reports failures in terms of the SQL it was handed (``Unknown
        column: x``, with a line/column offset). The planner never sees that
        SQL, so the message is rewritten in terms of the plan's own aliases and
        the schema the retriever supplied.
        """
        if alias and alias not in alias_to_cols:
            known = ", ".join(sorted(alias_to_cols)) or "none"
            return (
                f"Column '{column}' uses undeclared alias '{alias}'. "
                f"Declared table aliases: {known}."
            )

        if alias:
            return (
                f"Column '{column}' does not exist in table alias '{alias}'. "
                f"Available columns: {self._format_columns(alias_to_cols[alias])}."
            )

        matches = sorted(
            a for a, cols in alias_to_cols.items() if column.lower() in cols
        )
        if len(matches) > 1:
            return (
                f"Ambiguous column '{column}' referenced without alias. "
                f"Qualify it with one of: {', '.join(matches)}."
            )

        available = {f"{a}.{c}" for a, cols in alias_to_cols.items() for c in cols}
        return (
            f"Column '{column}' not found in any relevant table. "
            f"Available columns: {self._format_columns(available)}."
        )

    @staticmethod
    def _format_columns(columns: Set[str]) -> str:
        """Renders a bounded, deterministic list of column names for feedback."""
        ordered = sorted(columns)
        if not ordered:
            return "none"
        if len(ordered) > _MAX_HINTED_COLUMNS:
            return ", ".join(ordered[:_MAX_HINTED_COLUMNS]) + ", ..."
        return ", ".join(ordered)

    def _validate_columns(
        self, plan: PlanModel, alias_to_cols: Dict[str, Set[str]]
    ) -> List[str]:
        """Resolves every column reference in the plan via sqlglot's optimizer.

        ``qualify()`` is the sole authority on whether a reference resolves; it
        covers column existence, alias scoping and ambiguity. It fails on the
        first bad reference, so when it does fail each distinct reference is
        re-probed individually to report all of them at once.

        Returns:
            List[str]: Human-readable messages, one per unresolvable reference.
        """
        aliases = list(alias_to_cols)
        if not aliases or not plan.select_items:
            return []

        schema = {a: {c: "UNKNOWN" for c in cols} for a, cols in alias_to_cols.items()}
        query = self._build_validation_query(plan, aliases)

        try:
            qualify(query.copy(), schema=schema, validate_qualify_columns=True)
            return []
        except SqlglotError as exc:
            logger.debug("sqlglot qualify rejected plan: %s", exc)
            failure = exc

        messages: List[str] = []
        seen: Set[Tuple[str, str]] = set()
        for column in query.find_all(exp.Column):
            if isinstance(column.this, exp.Star):
                continue
            key = (column.table or "", column.name)
            if key in seen:
                continue
            seen.add(key)

            probe = exp.select(column.copy()).from_(
                exp.Table(this=exp.Identifier(this=aliases[0], quoted=False))
            )
            for alias in aliases[1:]:
                probe = probe.join(
                    exp.Table(this=exp.Identifier(this=alias, quoted=False)),
                    join_type="CROSS",
                )
            try:
                qualify(probe, schema=schema, validate_qualify_columns=True)
            except SqlglotError:
                messages.append(
                    self._describe_column_failure(key[0], key[1], alias_to_cols)
                )

        if not messages:
            messages.append(f"Plan columns could not be resolved: {failure}")
        return messages

    def _validate_ordinals(self, items: List[Any], label: str) -> Optional[PipelineError]:
        """Checks if ordinals in a list of items are contiguous starting from 0."""
        if not items:
            return None

        ords = [x.ordinal for x in items]
        expected = list(range(len(items)))

        if ords != expected:
            return PipelineError(
                node="logical_validator",
                message=f"{label} ordinals must be contiguous 0..{len(items)-1}, found {ords}",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
            )
        return None

    def _alias_collision(self, plan: PlanModel) -> Optional[PipelineError]:
        """Checks for duplicate table aliases in the plan."""
        seen = set()
        for t in plan.tables:
            if t.alias in seen:
                return PipelineError(
                    node="logical_validator",
                    message=f"Duplicate table alias '{t.alias}' in plan.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
                )
            seen.add(t.alias)
        return None

    @staticmethod
    def _distinct_function(plan: PlanModel) -> Optional[PipelineError]:
        """Rejects a function named DISTINCT.

        ``COUNT(DISTINCT(x))`` only happens to parse as a distinct count;
        DISTINCT is a flag on the aggregate, not a function.
        """
        for node in _plan_exprs(plan):
            if node.kind == "func" and str(node.func_name).strip().upper() == "DISTINCT":
                return PipelineError(
                    node="logical_validator",
                    message=(
                        "DISTINCT is not a function. For COUNT(DISTINCT x), set "
                        "distinct: true on the COUNT expression; for SELECT DISTINCT, "
                        "set the plan's distinct: true."
                    ),
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
                )
        return None

    @staticmethod
    def _date_operations(plan: PlanModel) -> Optional[PipelineError]:
        """Rejects a date operation whose unit or shape is not the portable one."""
        for node in _plan_exprs(plan):
            if node.kind != "func":
                continue
            date = date_operation(node.func_name, node.args)
            if date is None:
                continue
            _, unit, operand = date
            if unit in DATE_UNITS and operand is not None:
                continue
            return PipelineError(
                node="logical_validator",
                message=(
                    f"Unsupported date operation {node.func_name}. Dates use DATE_PART(unit, date), "
                    "an integer, or DATE_TRUNC(unit, date), a 'YYYY-MM-DD' date; the unit is a string "
                    f"literal, one of: {', '.join(DATE_UNITS)}."
                ),
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
            )
        return None

    @staticmethod
    def _unsupported_functions(plan: PlanModel) -> List[PipelineError]:
        """Rejects every function name outside the plan language's list.

        ``func_name`` is model text that becomes the function's name in the
        SQL, so only a plain identifier naming a known read-only function is
        let through. DISTINCT has its own, more specific message.
        """
        rejected: List[str] = []
        for node in _plan_exprs(plan):
            name = str(node.func_name or "")
            if node.kind != "func" or name.strip().upper() == "DISTINCT" or is_allowed_function(name):
                continue
            if name not in rejected:
                rejected.append(name)
        allowed = ", ".join(sorted(ALLOWED_FUNCTIONS - {"TUPLE", "LIST"}))
        return [
            PipelineError(
                node="logical_validator",
                message=f"Function {name!r} is not supported. Use one of: {allowed}.",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.UNSUPPORTED_FUNCTION,
            )
            for name in rejected
        ]

    def _validate_policy(self, state: SubgraphExecutionState) -> list[PipelineError]:
        """Validates that the query adheres to access control policies.

        Args:
            state (SubgraphExecutionState): Execution state containing user_context.

        Returns:
            list[PipelineError]: Errors if unauthorized tables are accessed.
        """
        plan = state.ast_planner_response.plan if state.ast_planner_response else None
        errors: list[PipelineError] = []

        user_ctx = state.user_context
        # An unknown role, or none, grants nothing: RBAC returns no tables and
        # every table in the plan is refused below like any other denial.
        allowed_tables = self.rbac.get_allowed_tables(user_ctx)
        roles = list(user_ctx.roles) if user_ctx else []
        role = ','.join(roles)

        # Resolve Datasource ID for Namespacing
        ds_id = state.sub_query.datasource_id if state.sub_query else None
        if not ds_id:
             # Fail Closed if we don't know the datasource (cannot enforce namespace)
             return [
                 PipelineError(
                    node="logical_validator",
                    message="Security Enforcement Failed: No sub_query datasource_id in state.",
                    severity=ErrorSeverity.CRITICAL,
                    error_code=ErrorCode.SECURITY_VIOLATION
                 )
             ]

        logger.debug("Policy validation context: Role=%s, Allowed=%s", role, allowed_tables)

        for t in plan.tables:
            # STRICT namespacing: "*", "<datasource>.*" or "<datasource>.<table>".
            if table_allowed(allowed_tables, ds_id, t.name):
                continue

            namespaced_name = f"{ds_id}.{t.name}"
            # The operator always gets the table and the role (log and trace);
            # the user gets them only when the deployment opts in, because
            # naming a table tells an unauthorised user that it exists.
            logger.warning("Policy denied role(s) %s access to '%s'.", roles, namespaced_name)
            if settings.rbac_refusal_names_tables:
                message = (
                    f"Role '{role or '(none)'}' denied access to '{namespaced_name}'. "
                    "Policy requires explicit 'datasource.table' allow."
                )
            else:
                message = REFUSAL_MESSAGE
            errors.append(
                PipelineError(
                    node="logical_validator",
                    message=message,
                    severity=ErrorSeverity.CRITICAL,
                    error_code=ErrorCode.SECURITY_VIOLATION,
                    details={"datasource_id": ds_id, "table": namespaced_name, "roles": roles},
                )
            )

        return errors

    def _validate_static(self, state: SubgraphExecutionState) -> list[PipelineError]:
        """Performs static structure validation on the plan.

        Checks:
        - Query type allowed (READ only).
        - Ordinal integrity.
        - Alias uniqueness.
        - Join alias validity.
        - No function named DISTINCT (it is the ``distinct`` flag).
        - Every other function name is a known one (``ast_planner.functions``).
        - Date operations name a portable unit (year, quarter, month, day).
        - Column existence and scoping (via sqlglot's qualify optimizer).
        """
        plan: PlanModel = state.ast_planner_response.plan if state.ast_planner_response else None
        errors: list[PipelineError] = []

        if not plan.tables:
            return [
                PipelineError(
                    node="logical_validator",
                    message="Plan has no tables.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
                )
            ]

        if plan.query_type != "READ":
            return [
                PipelineError(
                    node="logical_validator",
                    message=f"Query type '{plan.query_type}' not allowed.",
                    severity=ErrorSeverity.CRITICAL,
                    error_code=ErrorCode.SECURITY_VIOLATION,
                )
            ]

        for label, group in [
            ("tables", plan.tables),
            ("joins", plan.joins),
            ("select_items", plan.select_items),
            ("group_by", plan.group_by),
            ("order_by", plan.order_by),
        ]:
            err = self._validate_ordinals(group, label)
            if err:
                errors.append(err)

        alias_err = self._alias_collision(plan)
        if alias_err:
            errors.append(alias_err)

        distinct_err = self._distinct_function(plan)
        if distinct_err:
            errors.append(distinct_err)

        errors.extend(self._unsupported_functions(plan))

        date_err = self._date_operations(plan)
        if date_err:
            errors.append(date_err)

        if state.sub_query and state.sub_query.expected_schema:
            expected_names = [c.name for c in state.sub_query.expected_schema if c.name]
            actual_aliases = [s.alias for s in plan.select_items if s.alias]
            if len(plan.select_items) != len(expected_names):
                errors.append(
                    PipelineError(
                        node="logical_validator",
                        message=(
                            "Select item count must match expected_schema. "
                            f"Expected {len(expected_names)}, got {len(plan.select_items)}."
                        ),
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
                    )
                )
            if sorted(actual_aliases) != sorted(expected_names):
                errors.append(
                    PipelineError(
                        node="logical_validator",
                        message=(
                            "Select aliases must match expected_schema names. "
                            f"Expected {expected_names}, got {actual_aliases}."
                        ),
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
                    )
                )

        alias_to_cols, plan_aliases, alias_errors = self._resolve_plan_tables(state, plan)
        errors.extend(alias_errors)

        table_to_cols, relationships = self._build_allowed_schema(state)
        alias_to_table: Dict[str, str] = {
            t.alias: self._normalize_name(t.name) for t in plan.tables
        }

        for j in plan.joins:
            if j.left_alias not in plan_aliases:
                errors.append(
                    PipelineError(
                        node="logical_validator",
                        message=f"Join left alias '{j.left_alias}' not in plan tables.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.JOIN_TABLE_NOT_IN_PLAN,
                    )
                )

            if j.right_alias not in plan_aliases:
                errors.append(
                    PipelineError(
                        node="logical_validator",
                        message=f"Join right alias '{j.right_alias}' not in plan tables.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.JOIN_TABLE_NOT_IN_PLAN,
                    )
                )
            join_aliases = self._condition_aliases(j.condition)
            if j.left_alias not in join_aliases or j.right_alias not in join_aliases:
                errors.append(
                    PipelineError(
                        node="logical_validator",
                        message=(
                            "Join condition must reference both "
                            f"'{j.left_alias}' and '{j.right_alias}'."
                        ),
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
                    )
                )

            join_pairs = self._extract_join_pairs(j.condition)
            if not join_pairs:
                errors.append(
                    PipelineError(
                        node="logical_validator",
                        message="Join condition must include an equality between join columns.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
                    )
                )
            else:
                left_table = alias_to_table.get(j.left_alias, "")
                right_table = alias_to_table.get(j.right_alias, "")
                matched = False
                for left_alias, left_col, right_alias, right_col in join_pairs:
                    if left_alias != j.left_alias or right_alias != j.right_alias:
                        continue
                    for rel in relationships:
                        from_table = self._normalize_name(rel.get("from_table"))
                        to_table = self._normalize_name(rel.get("to_table"))
                        from_cols = [self._normalize_name(c) for c in rel.get("from_columns") or []]
                        to_cols = [self._normalize_name(c) for c in rel.get("to_columns") or []]
                        if (
                            left_table == from_table
                            and right_table == to_table
                            and left_col in from_cols
                            and right_col in to_cols
                        ):
                            matched = True
                            break
                        if (
                            left_table == to_table
                            and right_table == from_table
                            and left_col in to_cols
                            and right_col in from_cols
                        ):
                            matched = True
                            break
                    if matched:
                        break
                if not matched:
                    errors.append(
                        PipelineError(
                            node="logical_validator",
                            message="Join does not match any allowed relationship.",
                            severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
                        )
                    )

        column_messages = self._validate_columns(plan, alias_to_cols)

        column_severity = (
            ErrorSeverity.ERROR if self.strict_columns else ErrorSeverity.WARNING
        )
        for msg in column_messages:
            errors.append(
                PipelineError(
                    node="logical_validator",
                    message=msg,
                    severity=column_severity,
                    error_code=ErrorCode.COLUMN_NOT_FOUND,
                )
            )

        return errors



    def __call__(self, state: SubgraphExecutionState) -> Dict[str, Any]:
        """Executes the validation node.

        Args:
            state (SubgraphExecutionState): Current execution state.

        Returns:
            Dict[str, Any]: Validation results, including errors and reasoning.
        """
        node_name = "logical_validator"
        errors: list[PipelineError] = []

        try:
            logger.debug("Logical Validator received plan:")
            plan = state.ast_planner_response.plan if state.ast_planner_response else None
            if plan:
                logger.debug(plan.model_dump_json(indent=2))
            else:
                logger.warning("No plan to validate.")

            if not plan:
                return {
                    "logical_validator_response": LogicalValidatorResponse(
                        errors=[
                            PipelineError(
                                node=node_name,
                                message="Missing Plan",
                                severity=ErrorSeverity.CRITICAL,
                                error_code=ErrorCode.MISSING_PLAN,
                            )
                        ],
                        reasoning=[],
                        checks=[
                            ValidationCheck(
                                name="plan_present",
                                passed=False,
                                message="No plan to validate",
                            )
                        ],
                    ),
                    "errors": [
                        PipelineError(
                            node=node_name,
                            message="Missing Plan",
                            severity=ErrorSeverity.CRITICAL,
                            error_code=ErrorCode.MISSING_PLAN,
                        )
                    ],
                }

            # Static validation is isolated so that a failure inside it can
            # never skip policy enforcement. RBAC must run for every plan.
            static_errors: list[PipelineError] = []
            try:
                static_errors.extend(self._validate_static(state))
            except Exception as exc:
                logger.exception("Static logical validation crashed")
                static_errors.append(
                    PipelineError(
                        node=node_name,
                        message=f"Static logical validation crashed: {exc}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.VALIDATOR_CRASH,
                        stack_trace=traceback.format_exc(),
                    )
                )

            policy_errors = list(self._validate_policy(state))
            errors.extend(static_errors)
            errors.extend(policy_errors)

            role = ",".join(state.user_context.roles) if state.user_context else ""
            checks = [
                ValidationCheck(
                    name="plan_present",
                    passed=True,
                    message="Plan received from the planner",
                ),
                ValidationCheck(
                    name="structure_and_schema",
                    passed=not static_errors,
                    message=(
                        static_errors[0].message
                        if static_errors
                        else "Tables, columns and joins resolve against the retrieved schema"
                    ),
                ),
                ValidationCheck(
                    name="policy",
                    passed=not policy_errors,
                    message=(
                        policy_errors[0].message
                        if policy_errors
                        else f"Role '{role}' may read every table in the plan"
                    ),
                ),
            ]

            if any(e.severity in (ErrorSeverity.CRITICAL, ErrorSeverity.ERROR) for e in errors):
                response = LogicalValidatorResponse(
                    errors=errors,
                    reasoning=[{"node": node_name, "content": [e.message for e in errors]}],
                    checks=checks,
                )
                return {
                    "logical_validator_response": response,
                    "errors": errors,
                    "reasoning": response.reasoning,
                }

            reasoning = "Logical validation successful."

            response = LogicalValidatorResponse(
                errors=errors,
                reasoning=[{"node": node_name, "content": reasoning}],
                checks=checks,
            )
            return {
                "logical_validator_response": response,
                "errors": errors,
                "reasoning": response.reasoning,
            }

        except Exception as exc:
            logger.exception("Logical Validator crashed")
            error = PipelineError(
                node=node_name,
                message=f"Logical Validator crashed: {exc}",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.VALIDATOR_CRASH,
                stack_trace=traceback.format_exc(),
            )
            return {
                "logical_validator_response": LogicalValidatorResponse(errors=[error]),
                "errors": [error],
            }
