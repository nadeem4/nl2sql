from __future__ import annotations

from typing import Set, Dict, Any, List, Optional, Tuple, TYPE_CHECKING
import traceback

if TYPE_CHECKING:
    from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel, Expr
from nl2sql.context import NL2SQLContext
from nl2sql.common.logger import get_logger
from nl2sql.common.settings import settings
from nl2sql.pipeline.nodes.validator.schemas import LogicalValidatorResponse


logger = get_logger("logical_validator")


class ValidatorVisitor:
    """Traverses the Expr AST to validate column existence and scoping.

    Attributes:
        alias_to_cols (Dict[str, Set[str]]): Map of table aliases to their column names.
        errors (List[str]): List of validation error messages collected during traversal.
    """

    def __init__(self, alias_to_cols: Dict[str, Set[str]]):
        """Initializes the ValidatorVisitor.

        Args:
            alias_to_cols (Dict[str, Set[str]]): Mapping of aliases to available columns.
        """
        self.alias_to_cols = alias_to_cols
        self.errors: List[str] = []

    def visit(self, expr: Expr) -> None:
        """Recursively visits an expression node to check for column errors.

        Args:
            expr (Expr): The expression to validate.
        """
        kind = expr.kind

        if kind == "column":
            self._visit_column(expr)

        elif kind == "func":
            for arg in expr.args:
                self.visit(arg)

        elif kind == "binary":
            if expr.left:
                self.visit(expr.left)
            if expr.right:
                self.visit(expr.right)

        elif kind == "unary":
            if expr.expr:
                self.visit(expr.expr)

        elif kind == "case":
            if expr.whens:
                for when in expr.whens:
                    self.visit(when.condition)
                    self.visit(when.result)
            if expr.else_expr:
                self.visit(expr.else_expr)

        elif kind == "literal":
            return

    def _visit_column(self, col: Expr):
        """Validates a column expression against the known alias map."""
        if not col.column_name:
            return

        col_name = col.column_name.lower()

        if col.alias:
            alias = col.alias
            if alias not in self.alias_to_cols:
                self.errors.append(
                    f"Column '{col.column_name}' uses undeclared alias '{alias}'."
                )
                return

            allowed = self.alias_to_cols[alias]

            if col_name == "*":
                return

            if col_name != "*" and col_name not in allowed:
                self.errors.append(
                    f"Column '{col.column_name}' does not exist in table alias '{alias}'."
                )
            return

        if col_name == "*":
            return

        matches = [alias for alias, cols in self.alias_to_cols.items() if col_name in cols]

        if not matches:
            self.errors.append(
                f"Column '{col.column_name}' not found in any relevant table."
            )
        elif len(matches) > 1:
            self.errors.append(
                f"Ambiguous column '{col.column_name}' referenced without alias."
            )


class LogicalValidatorNode:
    """Validates the generated AST (PlanModel).

    Performs static validation on the AST structure and user policies.
    Physical validation (SQL syntax, dry run) happens in PhysicalValidatorNode.

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
        if not value:
            return ""
        return value.lower().split(".")[-1]

    def _build_allowed_schema(
        self, state: SubgraphExecutionState
    ) -> Tuple[Dict[str, Set[str]], Dict[str, Dict[str, Dict[str, Any]]], List[Dict[str, Any]]]:
        table_to_cols: Dict[str, Set[str]] = {}
        table_to_stats: Dict[str, Dict[str, Dict[str, Any]]] = {}
        relationships: List[Dict[str, Any]] = []

        for rt in state.relevant_tables:
            table_name = self._normalize_name(rt.name)
            if not table_name:
                continue
            cols: Set[str] = set()
            stats_map: Dict[str, Dict[str, Any]] = {}
            for c in rt.columns or []:
                col_name = self._normalize_name(c.name)
                if not col_name:
                    continue
                cols.add(col_name)
                stats = getattr(c, "stats", None)
                if isinstance(stats, dict) and stats:
                    stats_map[col_name] = stats
            table_to_cols[table_name] = cols
            table_to_stats[table_name] = stats_map

            for rel in getattr(rt, "relationships", []) or []:
                relationships.append(rel)

        return table_to_cols, table_to_stats, relationships

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

    def _extract_literal_checks(self, expr: Expr) -> List[Tuple[Optional[str], str, str, Any]]:
        checks: List[Tuple[Optional[str], str, str, Any]] = []

        def walk(node: Optional[Expr]) -> None:
            if not node:
                return
            if node.kind == "binary" and node.op in ("=", "IN", "LIKE"):
                left = node.left
                right = node.right
                if left and right:
                    if left.kind == "column" and right.kind == "literal":
                        if left.column_name:
                            checks.append((left.alias, self._normalize_name(left.column_name), node.op, right.value))
                    elif right.kind == "column" and left.kind == "literal":
                        if right.column_name:
                            checks.append((right.alias, self._normalize_name(right.column_name), node.op, left.value))
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
        return checks

    def _value_matches_stats(self, value: Any, stats: Dict[str, Any]) -> bool:
        samples = stats.get("sample_values") or []
        if not samples:
            return True
        if isinstance(value, str):
            return value.lower() in {str(s).lower() for s in samples}
        return value in samples

    def _like_matches_stats(self, value: Any, stats: Dict[str, Any]) -> bool:
        if not isinstance(value, str):
            return False
        samples = [str(s).lower() for s in (stats.get("sample_values") or [])]
        synonyms = [str(s).lower() for s in (stats.get("synonyms") or [])]
        candidates = samples + synonyms
        if not candidates:
            return True
        val = value.lower()
        return any(c in val or val in c for c in candidates)

    def _collect_aliases(self, expr: Expr) -> Set[str]:
        aliases: Set[str] = set()

        def walk(node: Optional[Expr]) -> None:
            if not node:
                return
            if node.kind == "column":
                if node.alias:
                    aliases.add(node.alias)
                return
            if node.kind == "func":
                for arg in node.args:
                    walk(arg)
                return
            if node.kind == "binary":
                walk(node.left)
                walk(node.right)
                return
            if node.kind == "unary":
                walk(node.expr)
                return
            if node.kind == "case":
                if node.whens:
                    for when in node.whens:
                        walk(when.condition)
                        walk(when.result)
                if node.else_expr:
                    walk(node.else_expr)
                return

        walk(expr)
        return aliases

    def _build_alias_map(
        self, state: SubgraphExecutionState, plan: PlanModel
    ) -> Tuple[Dict[str, Set[str]], Set[str], List[PipelineError]]:
        """Constructs a map of table aliases to column sets from the schema.

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
            simple_name = rt_name.split(".")[-1] if rt_name else ""
            if simple_name:
                simple_map.setdefault(simple_name, []).append(rt)
            if "." in rt_name:
                full_map[rt_name] = rt

        for t in plan.tables:
            plan_aliases.add(t.alias)

            found_table = None
            plan_simple = (t.name or "").lower()
            if t.schema_name or t.database:
                plan_key = self._normalize_table_key(
                    t.name, t.schema_name, t.database
                )
                found_table = full_map.get(plan_key)
                if not found_table:
                    candidates = simple_map.get(plan_simple, [])
                    if len(candidates) == 1:
                        found_table = candidates[0]
                    elif len(candidates) > 1:
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
            else:
                candidates = simple_map.get(plan_simple, [])
                if len(candidates) == 1:
                    found_table = candidates[0]
                elif len(candidates) > 1:
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

            cols: Set[str] = set()
            for c in found_table.columns:
                name = c.name.lower()
                if "." in name:
                    name = name.split(".")[-1]
                cols.add(name)

            alias_to_cols[t.alias] = cols

        logger.debug("Validator alias map: %s", alias_to_cols)
        return alias_to_cols, plan_aliases, errors

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
        allowed_tables = self.rbac.get_allowed_tables(user_ctx)
        role = ','.join(user_ctx.roles)
        
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

        if "*" in allowed_tables:
            return []

        for t in plan.tables:
            # STRICT Namespacing Logic
            namespaced_name = f"{ds_id}.{t.name}"
            ds_wildcard = f"{ds_id}.*"
            
            # Check 1: Exact Match (e.g. "sales_db.orders")
            if namespaced_name in allowed_tables:
                continue
                
            # Check 2: Datasource Wildcard (e.g. "sales_db.*")
            if ds_wildcard in allowed_tables:
                continue
                
            # If no match -> Violation
            errors.append(
                PipelineError(
                    node="logical_validator",
                    message=f"Role '{role}' denied access to '{namespaced_name}'. Policy requires explicit 'datasource.table' allow.",
                    severity=ErrorSeverity.CRITICAL,
                    error_code=ErrorCode.SECURITY_VIOLATION,
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
        - Column existence (via ValidatorVisitor).
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

        alias_to_cols, plan_aliases, alias_errors = self._build_alias_map(state, plan)
        errors.extend(alias_errors)

        table_to_cols, table_to_stats, relationships = self._build_allowed_schema(state)
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
            join_aliases = self._collect_aliases(j.condition)
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

        visitor = ValidatorVisitor(alias_to_cols)

        if plan.where:
            visitor.visit(plan.where)

        if plan.having:
            visitor.visit(plan.having)

        for s in plan.select_items:
            visitor.visit(s.expr)

        for g in plan.group_by:
            visitor.visit(g.expr)

        for o in plan.order_by:
            visitor.visit(o.expr)

        for j in plan.joins:
            visitor.visit(j.condition)

        for expr in [plan.where, plan.having]:
            if not expr:
                continue
            checks = self._extract_literal_checks(expr)
            for alias, col_name, op, value in checks:
                resolved_alias = alias
                if not resolved_alias:
                    matches = [
                        a for a, cols in alias_to_cols.items() if col_name in cols
                    ]
                    if len(matches) == 1:
                        resolved_alias = matches[0]
                if not resolved_alias:
                    continue
                table_name = alias_to_table.get(resolved_alias, "")
                stats = table_to_stats.get(table_name, {}).get(col_name)
                if not stats:
                    continue
                if op in ("=", "IN") and not self._value_matches_stats(value, stats):
                    errors.append(
                        PipelineError(
                            node="logical_validator",
                            message=(
                                f"Literal value '{value}' not found in stats for {table_name}.{col_name}."
                            ),
                            severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
                        )
                    )
                if op == "LIKE" and not self._like_matches_stats(value, stats):
                    errors.append(
                        PipelineError(
                            node="logical_validator",
                            message=(
                                f"LIKE pattern '{value}' is not derived from stats for {table_name}.{col_name}."
                            ),
                            severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
                        )
                    )

        column_severity = (
            ErrorSeverity.ERROR if self.strict_columns else ErrorSeverity.WARNING
        )
        for msg in visitor.errors:
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

            errors.extend(self._validate_static(state))
            errors.extend(self._validate_policy(state))

            if any(e.severity in (ErrorSeverity.CRITICAL, ErrorSeverity.ERROR) for e in errors):
                response = LogicalValidatorResponse(
                    errors=errors,
                    reasoning=[{"node": node_name, "content": [e.message for e in errors]}],
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
