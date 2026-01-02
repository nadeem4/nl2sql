from __future__ import annotations

from typing import Set, Dict, Any, List, Optional, Tuple
import traceback

from nl2sql.pipeline.state import GraphState
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.pipeline.nodes.planner.schemas import PlanModel, Expr
from nl2sql.datasources import DatasourceRegistry
from nl2sql.common.logger import get_logger


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

    def __init__(self, registry: DatasourceRegistry):
        """Initializes the LogicalValidatorNode.

        Args:
            registry (DatasourceRegistry): The registry of datasources.
        """
        self.registry = registry

    def _build_alias_map(
        self, state: GraphState, plan: PlanModel
    ) -> Tuple[Dict[str, Set[str]], Set[str], List[PipelineError]]:
        """Constructs a map of table aliases to column sets from the schema.

        Args:
            state (GraphState): Current execution state containing relevant_tables.
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

        for t in plan.tables:
            plan_aliases.add(t.alias)

            found_table = next(
                (rt for rt in state.relevant_tables if rt.name == t.name),
                None
            )

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

    def _validate_policy(self, state: GraphState) -> list[PipelineError]:
        """Validates that the query adheres to access control policies.

        Args:
            state (GraphState): Execution state containing user_context.

        Returns:
            list[PipelineError]: Errors if unauthorized tables are accessed.
        """
        plan = state.plan
        errors: list[PipelineError] = []

        user_ctx = state.user_context or {}
        allowed_tables = user_ctx.get("allowed_tables", [])
        role = user_ctx.get("role", "unknown")

        logger.debug("Policy validation context: %s", user_ctx)

        if "*" in allowed_tables:
            return []

        for t in plan.tables:
            if t.name not in allowed_tables:
                errors.append(
                    PipelineError(
                        node="logical_validator",
                        message=f"Role '{role}' not authorized to access '{t.name}'.",
                        severity=ErrorSeverity.CRITICAL,
                        error_code=ErrorCode.SECURITY_VIOLATION,
                    )
                )

        return errors

    def _validate_static(self, state: GraphState) -> list[PipelineError]:
        """Performs static structure validation on the plan.

        Checks:
        - Query type allowed (READ only).
        - Ordinal integrity.
        - Alias uniqueness.
        - Join alias validity.
        - Column existence (via ValidatorVisitor).
        """
        plan: PlanModel = state.plan
        errors: list[PipelineError] = []

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

        alias_to_cols, plan_aliases, alias_errors = self._build_alias_map(state, plan)
        errors.extend(alias_errors)

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

        for msg in visitor.errors:
            errors.append(
                PipelineError(
                    node="logical_validator",
                    message=msg,
                    severity=ErrorSeverity.WARNING,
                    error_code=ErrorCode.COLUMN_NOT_FOUND,
                )
            )

        return errors



    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """Executes the validation node.

        Args:
            state (GraphState): Current execution state.

        Returns:
            Dict[str, Any]: Validation results, including errors and reasoning.
        """
        node_name = "logical_validator"
        errors: list[PipelineError] = []

        try:
            logger.debug("Logical Validator received plan:")
            if state.plan:
                logger.debug(state.plan.model_dump_json(indent=2))
            else:
                logger.warning("No plan to validate.")

            if not state.plan:
                return {
                     "errors": [
                         PipelineError(node=node_name, message="Missing Plan", severity=ErrorSeverity.CRITICAL, error_code=ErrorCode.MISSING_PLAN)
                     ]
                }

            errors.extend(self._validate_static(state))
            errors.extend(self._validate_policy(state))

            if any(e.severity in (ErrorSeverity.CRITICAL, ErrorSeverity.ERROR) for e in errors):
                return {
                    "errors": errors,
                    "reasoning": [{"node": node_name, "content": [e.message for e in errors]}],
                }

            reasoning = "Logical validation successful."

            return {
                "errors": errors,
                "reasoning": [{"node": node_name, "content": reasoning}],
            }

        except Exception as exc:
            logger.exception("Logical Validator crashed")
            return {
                "errors": [
                    PipelineError(
                        node=node_name,
                        message=f"Logical Validator crashed: {exc}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.VALIDATOR_CRASH,
                        stack_trace=traceback.format_exc(),
                    )
                ]
            }
