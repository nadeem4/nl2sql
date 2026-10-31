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
    DIFF_UNITS,
    LEGACY_FUNCTIONS,
    date_diff,
    date_operation,
    is_allowed_function,
)
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, PlanModel, Expr
from nl2sql.pipeline.nodes.generator.node import (
    DATE_DIFF_USAGE,
    SUPPORTED_BINARY_OPS,
    GeneratorNode,
    MisplacedStar,
    SqlVisitor,
    ordered,
)
from nl2sql.runtime import Runtime
from nl2sql.common.logger import get_logger
from nl2sql.events import publish
from nl2sql.pipeline.nodes.validator.schemas import LogicalValidatorResponse, ValidationCheck
from nl2sql.pipeline.nodes.validator.join_keys import join_key_type_errors
from nl2sql.pipeline.nodes.validator.join_hints import join_hint, normalize_name


logger = get_logger("logical_validator")

_MAX_HINTED_COLUMNS = 15

# What a caller is told when the plan needs a table their role cannot read.
# It names nothing, so it reveals nothing about what exists; the table and
# role go to the log and, through the error's details, the run trace.
REFUSAL_MESSAGE = "You do not have permission to see the data this question requires."


def _walk(node: Optional[Expr]):
    """An expression and every expression nested in it."""
    if not node:
        return
    yield node
    for child in (*node.args, node.left, node.right, node.expr, node.else_expr):
        yield from _walk(child)
    for when in node.whens:
        yield from _walk(when.condition)
        yield from _walk(when.result)


def _plan_exprs(plan: PlanModel):
    """Every expression node in the plan, nested ones included."""
    walk = _walk
    roots = [s.expr for s in plan.select_items] + [g.expr for g in plan.group_by]
    roots += [o.expr for o in plan.order_by] + [j.condition for j in plan.joins]
    roots += [plan.where, plan.having]
    for root in roots:
        yield from walk(root)


class LogicalValidatorNode:
    """Validates the generated AST (PlanModel).

    Performs static validation on the AST structure and user policies.

    Attributes:
        registry (DatasourceRegistry): Registry to fetch schemas and profiles.
    """

    def __init__(self, ctx: Runtime):
        """Initializes the LogicalValidatorNode.

        Args:
            registry (DatasourceRegistry): The registry of datasources.
        """
        self.registry = ctx.ds_registry
        self.policy_engine = ctx.policy_engine
        self.strict_columns = ctx.settings.pipeline.strict_columns
        self.refusal_names_tables = ctx.config.policies.refusal_names_tables
        # The validator does not render SQL, but it builds the generator's
        # query tree to find out whether the plan *can* be rendered. See
        # ``_validate_buildable``.
        self._generator = GeneratorNode(ctx)

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
        return normalize_name(value)

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
        node = SqlVisitor().visit(condition)
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
        visitor = SqlVisitor(aliases=[t.alias for t in plan.tables])

        selects = [visitor.visit_select_item(s.expr, s.alias) for s in plan.select_items] or [exp.Star()]
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

    def _plan_dialect(self, state: SubgraphExecutionState) -> Optional[str]:
        """The sub-query datasource's dialect, looked up the way the generator does.

        Falls back to ``None`` (sqlglot's default dialect) when there is no
        datasource or the registry cannot produce an adapter. The dialect only
        affects rendering details the buildability check discards, so failing
        to find one must not fail validation.
        """
        datasource_id = state.sub_query.datasource_id if state.sub_query else None
        if not datasource_id:
            return None
        try:
            return self.registry.get_adapter(datasource_id).get_dialect()
        except Exception as exc:  # pragma: no cover - registry shapes vary
            logger.debug("No dialect for datasource %s: %s", datasource_id, exc)
            return None

    @staticmethod
    def _unsupported_operators(plan: PlanModel) -> List[PipelineError]:
        """Rejects a binary operator the generator has no way to render.

        ``Expr.op``'s Literal admits ``NOT``, but ``_visit_binary`` renders
        only :data:`SUPPORTED_BINARY_OPS`; anything else raises. This check runs
        before every check that walks the plan through ``SqlVisitor``, which is
        why ``op: "NOT"`` used to surface as a ``VALIDATOR_CRASH`` -- retryable
        only by accident, with a message the planner could not act on.
        """
        errors: List[PipelineError] = []
        seen: Set[str] = set()
        for expr in _plan_exprs(plan):
            if expr.kind != "binary" or not expr.op:
                continue
            op = str(expr.op).upper()
            if op in SUPPORTED_BINARY_OPS or op in seen:
                continue
            seen.add(op)
            errors.append(
                PipelineError(
                    node="logical_validator",
                    message=(
                        f"Operator '{expr.op}' is not a binary operator. "
                        f"Binary operators: {', '.join(sorted(SUPPORTED_BINARY_OPS))}. "
                        "To negate a condition, use a unary expression with op 'NOT'."
                    ),
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
                )
            )
        return errors

    def _misplaced_star(self, plan: PlanModel) -> Optional[PipelineError]:
        """Rejects a ``*`` anywhere but a bare select item or ``COUNT(*)``.

        The rule lives in ``SqlVisitor``; walking every expression of the plan
        through it (``_build_validation_query``) finds the star wherever it is.
        Other build failures are left to the checks that own them.
        """
        try:
            self._build_validation_query(plan, [t.alias for t in plan.tables])
        except MisplacedStar as exc:
            return PipelineError(
                node="logical_validator",
                message=str(exc),
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
            )
        except ValueError:
            pass
        return None

    def _describe_plan_joins(
        self, plan: PlanModel, relationships: List[Dict[str, Any]]
    ) -> str:
        """Names the plan's tables and the foreign keys that would join them.

        A structural join failure is only actionable if the planner is told
        which alias is which table and which columns connect them. The schema
        snapshot already carries the relationships, so they are rewritten into
        the plan's own vocabulary here -- the same move
        ``_describe_column_failure`` makes for columns.
        """
        tables = ", ".join(f"{t.alias} = {t.name}" for t in plan.tables) or "none"
        alias_by_table: Dict[str, List[str]] = {}
        for t in plan.tables:
            alias_by_table.setdefault(self._normalize_name(t.name), []).append(t.alias)

        keys: List[str] = []
        for rel in relationships:
            from_table = self._normalize_name(rel.get("from_table"))
            to_table = self._normalize_name(rel.get("to_table"))
            from_cols = rel.get("from_columns") or []
            to_cols = rel.get("to_columns") or []
            if from_table not in alias_by_table or to_table not in alias_by_table:
                continue
            for from_alias in alias_by_table[from_table]:
                for to_alias in alias_by_table[to_table]:
                    if from_alias == to_alias:
                        continue
                    for from_col, to_col in zip(from_cols, to_cols):
                        key = (
                            f"{from_alias} ({rel.get('from_table')}).{from_col} = "
                            f"{to_alias} ({rel.get('to_table')}).{to_col}"
                        )
                        if key not in keys:
                            keys.append(key)

        if keys:
            joins = f"Foreign keys between them: {'; '.join(sorted(keys))}."
        else:
            joins = "No foreign key is declared between them."
        return f"Plan tables: {tables}. {joins}"

    def _validate_buildable(
        self,
        state: SubgraphExecutionState,
        plan: PlanModel,
        relationships: List[Dict[str, Any]],
    ) -> Optional[PipelineError]:
        """Rejects a plan the generator could not turn into a query.

        The validator resolves columns against a cross-joined throw-away query
        (``_build_validation_query``), which is blind to join topology on
        purpose. The generator builds the real FROM clause, so an orphaned
        table, a stranded join, a duplicate join or an operator it cannot
        render used to pass here and fail one node later as a terminal
        ``SQL_GEN_FAILED`` -- after the last retry edge, where the planner can
        no longer be asked to fix it.

        Rather than restate those rules and let the two drift apart, the
        generator's own builder runs here and its tree is discarded. Anything
        it rejects is a malformed plan, reported as a retryable
        ``INVALID_PLAN_STRUCTURE`` so the planner gets another go.
        """
        try:
            self._generator._build_query(
                plan, int(plan.limit or 1000), self._plan_dialect(state)
            )
        except ValueError as exc:
            return PipelineError(
                node="logical_validator",
                message=f"{exc} {self._describe_plan_joins(plan, relationships)}",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
            )
        return None

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
            diff = date_diff(node.func_name, node.args)
            if diff is not None:
                if diff[0] in DIFF_UNITS:
                    continue
                return PipelineError(
                    node="logical_validator",
                    message=f"Unsupported date operation {node.func_name}: {DATE_DIFF_USAGE}",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
                )
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
    def _date_subtraction(state: SubgraphExecutionState, plan: PlanModel) -> Optional[PipelineError]:
        """Rejects ``a - b`` of two date or timestamp columns.

        It is an interval on some databases and text arithmetic on others, and an
        interval cannot be compared with a number: use ``DATE_DIFF``.
        """
        types: Dict[str, Dict[str, str]] = {}
        for ref in plan.tables:
            for table in state.relevant_tables:
                name = (table.name or "").lower()
                wanted = (ref.name or "").lower()
                if ref.schema_name:
                    matches = name == f"{ref.schema_name}.{wanted}".lower()
                else:
                    matches = name.split(".")[-1] == wanted
                if matches:
                    types[ref.alias] = {c.name.lower(): (c.type or "").lower() for c in table.columns}

        def is_date(operand: Optional[Expr]) -> bool:
            if operand is None or operand.kind != "column":
                return False
            kind = types.get(operand.alias or "", {}).get((operand.column_name or "").lower(), "")
            return "date" in kind or "timestamp" in kind

        for node in _plan_exprs(plan):
            if node.kind == "binary" and node.op == "-" and is_date(node.left) and is_date(node.right):
                return PipelineError(
                    node="logical_validator",
                    message=f"Dates cannot be subtracted: {DATE_DIFF_USAGE}",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
                )
        return None

    @staticmethod
    def _label_without_key(state: SubgraphExecutionState, plan: PlanModel) -> Optional[PipelineError]:
        """Rejects a group-by label built from several columns of one table, without that table's key.

        ``first_name || last_name`` alone puts two people who share a name in one
        group. Grouping by one column, or by a date part, is not a label of this
        kind; a table with no known primary key is not judged.
        """
        plain = {(g.expr.alias, (g.expr.column_name or "").lower())
                 for g in plan.group_by if g.expr.kind == "column"}
        for g in plan.group_by:
            if g.expr.kind == "column":
                continue
            by_alias: Dict[str, Set[str]] = {}
            for node in _walk(g.expr):
                if node.kind == "column":
                    by_alias.setdefault(node.alias or "", set()).add((node.column_name or "").lower())
            for alias, columns in by_alias.items():
                if len(columns) < 2:
                    continue
                ref = next((t for t in plan.tables if t.alias == alias), None)
                table = next((t for t in state.relevant_tables
                              if ref and (t.name or "").lower().split(".")[-1] == (ref.name or "").lower()), None)
                key = [k for k in (table.primary_key if table else None) or []]
                missing = [k for k in key if (alias, k.lower()) not in plain]
                if missing:
                    return PipelineError(
                        node="logical_validator",
                        message=(
                            f"GROUP BY a label built from several columns of '{ref.name}' would merge different "
                            f"rows that share that label. Also group by its key ({', '.join(missing)}), "
                            "keeping the label in the select list."
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
        allowed = ", ".join(sorted(ALLOWED_FUNCTIONS - LEGACY_FUNCTIONS - {"TUPLE", "LIST"}))
        return [
            PipelineError(
                node="logical_validator",
                message=f"Function {name!r} is not supported. Use one of: {allowed}.",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.UNSUPPORTED_FUNCTION,
            )
            for name in rejected
        ]

    def _validate_policy(self, state: SubgraphExecutionState, audit: bool = True) -> list[PipelineError]:
        """Validates that the query adheres to access control policies.

        Args:
            state (SubgraphExecutionState): Execution state containing user_context.
            audit (bool): Log the denial as a warning and publish ``policy.decision``. Off for a plan
                that is only being checked (a clarifier's alternative), which reaches no caller.

        Returns:
            list[PipelineError]: Errors if unauthorized tables are accessed.
        """
        plan = state.ast_planner_response.plan if state.ast_planner_response else None
        errors: list[PipelineError] = []

        user_ctx = state.user_context
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

        # An unknown role, or none, grants nothing: its scope allows no table,
        # and every table in the plan is refused below like any other denial.
        # The schema retriever never showed the planner a table the scope
        # hides, so a plan naming one anyway is refused here.
        scope = self.policy_engine.scope(user_ctx, ds_id)
        published = set()  # a self-join names a table twice; the operator hears of it once
        for t in plan.tables:
            # The schema the generator will write, or the default one.
            rule = scope.rule_for(t.schema_name, t.name)
            if rule is None:
                continue

            # A table outside the default schema is named with its schema.
            outside_default = t.schema_name and t.schema_name.lower() != scope.default_schema.lower()
            namespaced_name = f"{ds_id}.{t.schema_name}.{t.name}" if outside_default else f"{ds_id}.{t.name}"
            # The operator always gets the table, the role and the rule (log
            # and trace); the user gets the table only when the deployment opts
            # in, because naming a table tells an unauthorised user that it exists.
            if audit:
                logger.warning("Policy denied role(s) %s access to '%s': %s.", roles, namespaced_name, rule)
            else:
                logger.debug("An alternative reading names '%s', which role(s) %s may not read.",
                             namespaced_name, roles)
            decision = (t.schema_name or scope.default_schema, t.name, rule)
            if audit and decision not in published:
                published.add(decision)
                publish("policy.decision", {"datasource_id": ds_id, "schema": decision[0], "table": t.name,
                                            "rule": rule, "step": "logical_validator"})
            if self.refusal_names_tables:
                message = f"Role '{role or '(none)'}' denied access to '{namespaced_name}'."
            else:
                message = REFUSAL_MESSAGE
            errors.append(
                PipelineError(
                    node="logical_validator",
                    message=message,
                    severity=ErrorSeverity.CRITICAL,
                    error_code=ErrorCode.SECURITY_VIOLATION,
                    details={"datasource_id": ds_id, "table": namespaced_name, "roles": roles, "rule": rule},
                )
            )

        return errors

    def _validate_static(self, state: SubgraphExecutionState) -> list[PipelineError]:
        """Performs static structure validation on the plan.

        Checks:
        - Alias uniqueness.
        - Join alias validity.
        - No function named DISTINCT (it is the ``distinct`` flag).
        - Every other function name is a known one (``ast_planner.functions``).
        - Date operations name a portable unit (year, quarter, month, day), and
          dates are never subtracted (``DATE_DIFF``).
        - Every binary operator is one the generator can render.
        - Column existence and scoping (via sqlglot's qualify optimizer).
        - The plan builds into a query at all (``_validate_buildable``): every
          table reachable from the FROM table, every join connecting to it
          exactly once.
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

        alias_err = self._alias_collision(plan)
        if alias_err:
            errors.append(alias_err)

        distinct_err = self._distinct_function(plan)
        if distinct_err:
            errors.append(distinct_err)

        errors.extend(self._unsupported_functions(plan))

        # Before anything that walks the plan through ``SqlVisitor``: an
        # operator it cannot render makes every one of those checks raise.
        operator_errors = self._unsupported_operators(plan)
        if operator_errors:
            return errors + operator_errors

        # The same reason: a misplaced star makes every walk raise.
        star_err = self._misplaced_star(plan)
        if star_err:
            return errors + [star_err]

        date_err = self._date_operations(plan)
        if date_err:
            # A date operation the generator cannot render makes every walk raise.
            return errors + [date_err]

        subtraction_err = self._date_subtraction(state, plan)
        if subtraction_err:
            errors.append(subtraction_err)

        label_err = self._label_without_key(state, plan)
        if label_err:
            errors.append(label_err)

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
                    # An equality reads the same either way round: `b.x = a.y` joins `a` to `b` as `a.y = b.x` does.
                    if (left_alias, right_alias) == (j.right_alias, j.left_alias):
                        left_alias, left_col, right_alias, right_col = right_alias, right_col, left_alias, left_col
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
                            message="Join does not match any allowed relationship."
                            + (
                                " " + join_hint(relationships, left_table, right_table, self._normalize_name,
                                                set(table_to_cols))
                                if left_table and right_table else ""
                            ),
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

        # A join key whose column cannot take its declared type (D30).
        errors.extend(join_key_type_errors(plan, state.sub_query, state.relevant_tables, self._plan_dialect(state)))

        # Last, so every check above keeps its own error code: whatever the
        # generator would still refuse to build.
        build_error = self._validate_buildable(state, plan, relationships)
        if build_error:
            errors.append(build_error)

        return errors



    def _findings(self, state: SubgraphExecutionState,
                  audit: bool = True) -> Tuple[List[PipelineError], List[PipelineError]]:
        """The policy and the static findings on the state's plan.

        Static validation is isolated so that a failure inside it can never
        skip policy enforcement. RBAC must run for every plan.
        """
        static_errors: List[PipelineError] = []
        try:
            static_errors.extend(self._validate_static(state))
        except Exception as exc:
            logger.exception("Static logical validation crashed")
            static_errors.append(
                PipelineError(
                    node="logical_validator",
                    message=f"Static logical validation crashed: {exc}",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.VALIDATOR_CRASH,
                    stack_trace=traceback.format_exc(),
                )
            )
        return list(self._validate_policy(state, audit)), static_errors

    def check(self, plan: PlanModel, state: SubgraphExecutionState, *, audit: bool = True) -> List[PipelineError]:
        """The validator's checks on ``plan`` as a function: every finding, the refusal first.

        ``plan`` is checked as a plan of ``state``'s sub-query, with its
        retrieved schema and its caller's scope, exactly as the node checks
        the state's own plan; the state is not changed. The clarifier runs
        each of the planner's alternatives through it (docs/architecture/wave_b.md,
        section 7), so an option is offered only when the node would pass it.
        An ERROR or CRITICAL finding means the plan would not pass.
        ``audit=False`` (the clarifier's) publishes no ``policy.decision``
        and logs no denial warning: a reading that is only checked, and
        dropped, reaches no caller.
        """
        probe = state.model_copy(update={"ast_planner_response": ASTPlannerResponse(plan=plan)})
        policy_errors, static_errors = self._findings(probe, audit)
        return policy_errors + static_errors

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

            policy_errors, static_errors = self._findings(state)
            # The refusal comes first: a table the scope hid from the planner
            # is also missing from its schema, and the denial is the reason.
            errors.extend(policy_errors)
            errors.extend(static_errors)

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
