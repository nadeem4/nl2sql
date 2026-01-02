from __future__ import annotations

from typing import Set, Dict, Any, List, Optional
import traceback

from nl2sql.pipeline.state import GraphState
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.pipeline.nodes.planner.schemas import PlanModel, Expr
from nl2sql.datasources import DatasourceRegistry
from nl2sql.common.logger import get_logger


logger = get_logger("validator")


class ValidatorVisitor:
    def __init__(self, alias_to_cols: Dict[str, Set[str]]):
        self.alias_to_cols = alias_to_cols
        self.errors: List[str] = []

    def visit(self, expr: Expr) -> None:
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


class ValidatorNode:
    def __init__(self, registry: DatasourceRegistry, row_limit: int | None = None):
        self.registry = registry
        self.row_limit = row_limit

    def _build_alias_map(
        self, state: GraphState, plan: PlanModel
    ) -> tuple[Dict[str, Set[str]], Set[str], List[PipelineError]]:

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
                        node="validator",
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
        if not items:
            return None

        ords = [x.ordinal for x in items]
        expected = list(range(len(items)))

        if ords != expected:
            return PipelineError(
                node="validator",
                message=f"{label} ordinals must be contiguous 0..{len(items)-1}, found {ords}",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
            )
        return None

    def _alias_collision(self, plan: PlanModel) -> Optional[PipelineError]:
        seen = set()
        for t in plan.tables:
            if t.alias in seen:
                return PipelineError(
                    node="validator",
                    message=f"Duplicate table alias '{t.alias}' in plan.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
                )
            seen.add(t.alias)
        return None

    def _validate_policy(self, state: GraphState) -> list[PipelineError]:
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
                        node="validator",
                        message=f"Role '{role}' not authorized to access '{t.name}'.",
                        severity=ErrorSeverity.CRITICAL,
                        error_code=ErrorCode.SECURITY_VIOLATION,
                    )
                )

        return errors

    def _validate_static(self, state: GraphState) -> list[PipelineError]:
        plan: PlanModel = state.plan
        errors: list[PipelineError] = []

        if plan.query_type != "READ":
            return [
                PipelineError(
                    node="validator",
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
                        node="validator",
                        message=f"Join left alias '{j.left_alias}' not in plan tables.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.JOIN_TABLE_NOT_IN_PLAN,
                    )
                )

            if j.right_alias not in plan_aliases:
                errors.append(
                    PipelineError(
                        node="validator",
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
                    node="validator",
                    message=msg,
                    severity=ErrorSeverity.WARNING,
                    error_code=ErrorCode.COLUMN_NOT_FOUND,
                )
            )

        return errors

    def _validate_semantic(self, state: GraphState) -> list[PipelineError]:
        errors: list[PipelineError] = []

        sql = getattr(state, "generated_sql", None)
        if not sql:
            return errors

        try:
            adapter = self.registry.get_adapter(state.selected_datasource_id)
        except Exception as e:
            logger.warning("Semantic validation skipped: %s", e)
            return errors

        try:
            caps = adapter.capabilities()
            if caps.supports_dry_run:
                res = adapter.dry_run(sql)
                if not res.is_valid:
                    errors.append(
                        PipelineError(
                            node="validator",
                            message=f"Dry Run Failed: {res.error_message}",
                            severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.EXECUTION_ERROR,
                        )
                    )
        except Exception as e:
            logger.warning("Dry run skipped: %s", e)

        return errors

    def _validate_perf(self, state: GraphState) -> list[PipelineError]:
        errors: list[PipelineError] = []

        sql = getattr(state, "generated_sql", None)
        if not sql:
            return errors

        try:
            adapter = self.registry.get_adapter(state.selected_datasource_id)
        except Exception:
            return errors

        try:
            cost = adapter.cost_estimate(sql)

            if self.row_limit and cost.estimated_rows > self.row_limit:
                errors.append(
                    PipelineError(
                        node="validator",
                        message=f"Estimated {cost.estimated_rows} rows exceeds configured limit {self.row_limit}",
                        severity=ErrorSeverity.WARNING,
                        error_code=ErrorCode.PERFORMANCE_WARNING,
                    )
                )

            if cost.estimated_rows > 1_000_000:
                plan = adapter.explain(sql)
                errors.append(
                    PipelineError(
                        node="validator",
                        message=f"Potential heavy query: {cost.estimated_rows} rows. Plan: {plan.plan_text[:250]}",
                        severity=ErrorSeverity.WARNING,
                        error_code=ErrorCode.PERFORMANCE_WARNING,
                    )
                )

        except Exception as e:
            logger.warning("Perf validation skipped: %s", e)

        return errors

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        node_name = "validator"
        errors: list[PipelineError] = []

        try:
            logger.debug("Validator received plan:")
            logger.debug(state.plan.model_dump_json(indent=2))

            errors.extend(self._validate_static(state))
            errors.extend(self._validate_policy(state))

            if any(e.severity in (ErrorSeverity.CRITICAL, ErrorSeverity.ERROR) for e in errors):
                return {
                    "errors": errors,
                    "reasoning": [{"node": node_name, "content": [e.message for e in errors]}],
                }

            errors.extend(self._validate_semantic(state))

            if any(e.severity in (ErrorSeverity.CRITICAL, ErrorSeverity.ERROR) for e in errors):
                return {
                    "errors": errors,
                    "reasoning": [{"node": node_name, "content": [e.message for e in errors]}],
                }

            errors.extend(self._validate_perf(state))

            reasoning = (
                "Validation successful."
                if not errors
                else [e.message for e in errors]
            )

            return {
                "errors": errors,
                "reasoning": [{"node": node_name, "content": reasoning}],
            }

        except Exception as exc:
            logger.exception("Validator crashed")
            return {
                "errors": [
                    PipelineError(
                        node=node_name,
                        message=f"Validator crashed: {exc}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.VALIDATOR_CRASH,
                        stack_trace=traceback.format_exc(),
                    )
                ]
            }
