from __future__ import annotations

import json
from typing import Set, Dict, Any, List
from datetime import datetime

from nl2sql.pipeline.state import GraphState
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.pipeline.nodes.planner.schemas import PlanModel, Expr
from nl2sql.datasources import DatasourceRegistry
from nl2sql.common.logger import get_logger

logger = get_logger("validator")


class ValidatorVisitor:
    """Recursively validates Expr nodes against the schema context."""
    def __init__(self, alias_to_cols: Dict[str, Set[str]]):
        self.alias_to_cols = alias_to_cols
        self.errors: List[str] = []

    def visit(self, expr: Expr) -> None:
        if expr.kind == "column":
            self._visit_column(expr)
        elif expr.kind == "func":
            for arg in expr.args:
                self.visit(arg)
        elif expr.kind == "binary":
            if expr.left: self.visit(expr.left)
            if expr.right: self.visit(expr.right)
        elif expr.kind == "unary":
            target = expr.expr or expr.left
            if target: self.visit(target)
        elif expr.kind == "case":
            if expr.whens:
                for when in expr.whens:
                    self.visit(when.operand)
                    self.visit(when.result)
            if expr.else_expr:
                self.visit(expr.else_expr)
        elif expr.kind == "literal":
            pass # Literals are safe
        else:
            pass

    def _visit_column(self, col: Expr):
        # Case Insensitive Compare
        col_name = col.column_name.lower() if col.column_name else ""
        
        # 1. Explicit Alias Check
        if col.alias:
            if col.alias not in self.alias_to_cols:
                self.errors.append(f"Column '{col.column_name}' uses undeclared alias '{col.alias}'")
            else:
                allowed_cols = self.alias_to_cols[col.alias]
                if col_name not in allowed_cols and col_name != "*":
                    self.errors.append(f"Column '{col.column_name}' does not exist in table alias '{col.alias}'")
        
        # 2. Implicit Check (No alias provided)
        else:
            if col_name != "*":
                found = False
                for cols in self.alias_to_cols.values():
                    if col_name in cols:
                        found = True
                        break
                if not found:
                    self.errors.append(f"Column '{col.column_name}' not found in any relevant table")


class ValidatorNode:
    """
    Validates the generated execution plan against schema, entity coverage,
    and security policies using Recursive AST traversal.
    """

    def __init__(self, registry: DatasourceRegistry, row_limit: int | None = None):
        self.registry = registry
        self.row_limit = row_limit

    def _validate_policy(self, state: GraphState) -> list[PipelineError]:
        plan = state.plan
        errors: list[PipelineError] = []
        user_ctx = state.user_context or {}
        print(f"user_ctx: {state.user_context}")
        allowed_tables = user_ctx.get("allowed_tables", [])
        
        if "*" in allowed_tables:
            return []
            
        for t in plan.tables:
            if t.name not in allowed_tables:
                user_role = user_ctx.get("role", "unknown")
                errors.append(
                PipelineError(
                    node="validator",
                    message=f"Access Denied: User role '{user_role}' is not authorized to access table '{t.name}'.",
                    severity=ErrorSeverity.CRITICAL,
                    error_code=ErrorCode.SECURITY_VIOLATION,
                )
            )
        return errors

    def _validate_static_analysis(self, state: GraphState) -> list[PipelineError]:
        """Layer 1: Static checks using Visitor for recursive AST."""
        plan = state.plan
        errors: list[PipelineError] = []

        if plan.query_type != "READ":
            return [PipelineError(node="validator", message=f"Query type '{plan.query_type}' not allowed.", severity=ErrorSeverity.CRITICAL, error_code=ErrorCode.SECURITY_VIOLATION)]

        # 1. Build Verification Map
        alias_to_cols: Dict[str, Set[str]] = {}
        plan_aliases: Set[str] = set()

        for t in plan.tables:
            plan_aliases.add(t.alias)
            
            # Verify table exists in relevant_tables
            found_table = next((rt for rt in state.relevant_tables if rt.name == t.name), None)
            if not found_table:
                errors.append(PipelineError(node="validator", message=f"Table '{t.name}' not found in relevant tables.", severity=ErrorSeverity.ERROR, error_code=ErrorCode.TABLE_NOT_FOUND))
            else:
                # Case Insensitive Storage
                alias_to_cols[t.alias] = {c.name.split(".")[1].lower() for c in found_table.columns}

        print(f"alias_to_cols: {alias_to_cols}")           
        # 2. Visit AST
        visitor = ValidatorVisitor(alias_to_cols)
        
        # Visit all expression trees
        if plan.where: visitor.visit(plan.where)
        if plan.having: visitor.visit(plan.having)
        
        for item in plan.select_items:
            visitor.visit(item.expr)
            
        for g in plan.group_by:
            visitor.visit(g.expr)
            
        for o in plan.order_by:
            visitor.visit(o.expr)
            
        for j in plan.joins:
            visitor.visit(j.condition)
            
            # Validate Join Aliases
            if j.left_alias not in plan_aliases:
                 errors.append(PipelineError(node="validator", message=f"Join left alias '{j.left_alias}' not defined in tables list.", severity=ErrorSeverity.WARNING, error_code=ErrorCode.JOIN_TABLE_NOT_IN_PLAN))
            
            if j.right_alias not in plan_aliases:
                 errors.append(PipelineError(node="validator", message=f"Join right alias '{j.right_alias}' not defined in tables list.", severity=ErrorSeverity.WARNING, error_code=ErrorCode.JOIN_TABLE_NOT_IN_PLAN))

        # 3. Convert Visitor Errors
        for msg in visitor.errors:
            errors.append(PipelineError(node="validator", message=msg, severity=ErrorSeverity.WARNING, error_code=ErrorCode.COLUMN_NOT_FOUND))

        return errors

    def _validate_semantic_correctness(self, state: GraphState) -> list[PipelineError]:
        """Layer 2: Semantic checks (Dry Run)."""
        errors = []
        if hasattr(state, "generated_sql") and state.generated_sql:
            try:
                adapter = self.registry.get_adapter(state.selected_datasource_id)
                if adapter.capabilities().supports_dry_run:
                    dry_result = adapter.dry_run(state.generated_sql)
                    if not dry_result.is_valid:
                        errors.append(PipelineError(node="validator", message=f"Dry Run Failed: {dry_result.error_message}", severity=ErrorSeverity.ERROR, error_code=ErrorCode.EXECUTION_ERROR))
            except Exception as e:
                logger.warning(f"Dry run skipped: {e}")
        return errors

    def _validate_performance(self, state: GraphState) -> list[PipelineError]:
        """Layer 3: Performance checks."""
        errors = []
        if hasattr(state, "generated_sql") and state.generated_sql:
             try:
                 adapter = self.registry.get_adapter(state.selected_datasource_id)
                 cost = adapter.cost_estimate(state.generated_sql)
                 if cost.estimated_rows > 1_000_000:
                     plan = adapter.explain(state.generated_sql)
                     plan_txt = plan.plan_text[:300]
                     errors.append(PipelineError(node="validator", message=f"Performance Warning: {cost.estimated_rows} rows. Plan: {plan_txt}", severity=ErrorSeverity.WARNING, error_code=ErrorCode.PERFORMANCE_WARNING))
             except Exception as e:
                 logger.warning(f"Perf check skipped: {e}")
        return errors

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        node_name = self.__class__.__name__.lower()
        errors: list[PipelineError] = []

        try:
            plan = state.plan
            print(f"plan: {plan.model_dump_json(indent=2)}")
            errors.extend(self._validate_static_analysis(state))
            errors.extend(self._validate_policy(state))
            
            if any(e.severity in (ErrorSeverity.CRITICAL, ErrorSeverity.ERROR) for e in errors):
                return {"errors": errors, "reasoning": [{"node": node_name, "content": [e.message for e in errors]}]}

            errors.extend(self._validate_semantic_correctness(state))
            if any(e.severity in (ErrorSeverity.CRITICAL, ErrorSeverity.ERROR) for e in errors):
                 return {"errors": errors, "reasoning": [{"node": node_name, "content": [e.message for e in errors]}]}

            errors.extend(self._validate_performance(state))

            return {
                "errors": errors,
                "reasoning": [{"node": node_name, "content": "Validation successful." if not errors else [e.message for e in errors]}]
            }

        except Exception as exc:
            logger.error(exc)
            return {
                "errors": [PipelineError(node=node_name, message=f"Validator crashed: {exc}", severity=ErrorSeverity.ERROR, error_code=ErrorCode.VALIDATOR_CRASH, stack_trace=str(exc))]
            }
