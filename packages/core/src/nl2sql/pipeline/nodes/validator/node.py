from __future__ import annotations

import json
from typing import Set, Dict, Any
from datetime import datetime

from nl2sql.pipeline.state import GraphState
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.pipeline.nodes.planner.schemas import PlanModel
from nl2sql.datasources import DatasourceRegistry
from nl2sql.common.logger import get_logger

logger = get_logger("validator")


class ValidatorNode:
    """
    Validates the generated execution plan against schema, entity coverage,
    and security policies.
    """

    def __init__(self, registry: DatasourceRegistry, row_limit: int | None = None):
        self.registry = registry
        self.row_limit = row_limit

    def is_aggregation(self, expr: str) -> bool:
        expr = expr.upper()
        return any(fn in expr for fn in ("COUNT(", "SUM(", "AVG(", "MIN(", "MAX("))

    def validate_expr_ref(
        self,
        expr: str,
        schema_cols: Set[str],
        plan_aliases: Set[str],
        errors: list[PipelineError],
        allow_derived: bool = False,
    ) -> None:
        if allow_derived:
            return

        if expr not in schema_cols:
            errors.append(
                PipelineError(
                    node="validator",
                    message=f"Column '{expr}' not found in schema.",
                    severity=ErrorSeverity.WARNING,
                    error_code=ErrorCode.COLUMN_NOT_FOUND,
                )
            )
            return

        if "." in expr:
            alias = expr.split(".", 1)[0]
            if alias not in plan_aliases:
                errors.append(
                    PipelineError(
                        node="validator",
                        message=f"Column '{expr}' uses undeclared alias '{alias}'.",
                        severity=ErrorSeverity.WARNING,
                        error_code=ErrorCode.INVALID_ALIAS_USAGE,
                    )
                )

    def validate_aggregations(self, plan: PlanModel, errors: list[PipelineError]) -> None:
        has_agg = False
        non_agg = []

        for col in plan.select_columns:
            if col.is_derived or self.is_aggregation(col.expr):
                has_agg = True
            else:
                non_agg.append(col.expr)

        group_exprs = {g.expr for g in plan.group_by}

        if has_agg or plan.group_by:
            for expr in non_agg:
                if expr not in group_exprs:
                    errors.append(
                        PipelineError(
                            node="validator",
                            message=f"Column '{expr}' must appear in GROUP BY or be aggregated.",
                            severity=ErrorSeverity.WARNING,
                            error_code=ErrorCode.MISSING_GROUP_BY,
                        )
                    )

    def validate_data_types(
        self,
        plan: PlanModel,
        relevant_tables: list[TableModel],
        profile_id: str,
        errors: list[PipelineError],
    ) -> None:
        try:
            profile = self.registry.get_profile(profile_id)
            date_format = profile.date_format or "ISO 8601"
        except Exception:
            date_format = "ISO 8601"

        col_types: Dict[str, str] = {}
        for table in relevant_tables:
            for col in table.columns:
                col_types[col.name] = col.type.upper()

        def check(expr: str, value: Any):
            if expr not in col_types:
                return

            sql_type = col_types[expr]

            if "DATE" in sql_type or "TIME" in sql_type:
                if isinstance(value, str):
                    raw = value.strip("'").strip('"')
                    try:
                        if "ISO" in date_format:
                            datetime.strptime(raw, "%Y-%m-%d")
                        else:
                            datetime.strptime(raw, date_format)
                    except Exception:
                        errors.append(
                            PipelineError(
                                node="validator",
                                message=f"Invalid date literal '{value}' for column '{expr}'.",
                                severity=ErrorSeverity.WARNING,
                                error_code=ErrorCode.INVALID_DATE_FORMAT,
                            )
                        )

            if any(t in sql_type for t in ("INT", "DECIMAL", "FLOAT")):
                if isinstance(value, str):
                    raw = value.strip("'").strip('"')
                    if not raw.replace(".", "", 1).isdigit():
                        errors.append(
                            PipelineError(
                                node="validator",
                                message=f"Invalid numeric literal '{value}' for column '{expr}'.",
                                severity=ErrorSeverity.WARNING,
                                error_code=ErrorCode.INVALID_NUMERIC_VALUE,
                            )
                        )

        for f in plan.filters:
            check(f.column.expr, f.value)

        for h in plan.having:
            check(h.expr, h.value)

    def _validate_policy(self, state: GraphState, plan: PlanModel) -> list[PipelineError]:
        """
        Layer 1.5: Policy/AuthZ checks.
        Validates if the user is allowed to access the tables in the plan.
        """
        node_name = "validator"
        errors: list[PipelineError] = []
        
        user_ctx = state.user_context or {}
        allowed_tables = user_ctx.get("allowed_tables", [])
        
        # If Wildcard "*", allow everything (Admin)
        if "*" in allowed_tables:
            return []
            
        # Check every table in the plan
        for t in plan.tables:
            # We check strict equality for now. 
            # In future, might need more robust matching (schema.table vs table).
            if t.name not in allowed_tables:
                 user_role = user_ctx.get("role", "unknown")
                 errors.append(
                    PipelineError(
                        node=node_name,
                        message=f"Access Denied: User role '{user_role}' is not authorized to access table '{t.name}'.",
                        severity=ErrorSeverity.CRITICAL,
                        error_code=ErrorCode.SECURITY_VIOLATION,
                    )
                )
        return errors

    def _validate_static_analysis(self, state: GraphState, plan: PlanModel) -> list[PipelineError]:
        """
        Layer 1: Static checks (Schema existence, Aliases, Types, SQL Logic).
        """
        node_name = "validator"
        errors: list[PipelineError] = []

        if plan.query_type != "READ":
            errors.append(
                PipelineError(
                    node=node_name,
                    message=f"Query type '{plan.query_type}' not allowed.",
                    severity=ErrorSeverity.CRITICAL,
                    error_code=ErrorCode.SECURITY_VIOLATION,
                )
            )
            return errors

        schema_cols: Set[str] = set()
        plan_aliases: Set[str] = set()
        plan_tables: Set[str] = set()

        for t in plan.tables:
            plan_aliases.add(t.alias)
            plan_tables.add(t.name)

            found = False
            for st in state.relevant_tables:
                if st.name == t.name and st.alias == t.alias:
                    for c in st.columns:
                        schema_cols.add(c.name)
                    found = True
                    break

            if not found:
                errors.append(
                    PipelineError(
                        node=node_name,
                        message=f"Table '{t.name}' with alias '{t.alias}' not found in schema.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.TABLE_NOT_FOUND,
                    )
                )

        for col in plan.select_columns:
            self.validate_expr_ref(
                col.expr,
                schema_cols,
                plan_aliases,
                errors,
                allow_derived=bool(col.is_derived or self.is_aggregation(col.expr)),
            )

        for flt in plan.filters:
            if flt.column.alias:
                 errors.append(
                    PipelineError(
                        node=node_name,
                        message=f"Aliases are only allowed in 'select_columns', not in filters (found '{flt.column.alias}').",
                        severity=ErrorSeverity.WARNING,
                        error_code=ErrorCode.INVALID_ALIAS_USAGE,
                    )
                )
            self.validate_expr_ref(
                flt.column.expr,
                schema_cols,
                plan_aliases,
                errors,
                allow_derived=False,
            )

        for gb in plan.group_by:
            if gb.alias:
                 errors.append(
                    PipelineError(
                        node=node_name,
                        message=f"Aliases are only allowed in 'select_columns', not in group_by (found '{gb.alias}').",
                        severity=ErrorSeverity.WARNING,
                        error_code=ErrorCode.INVALID_ALIAS_USAGE,
                    )
                )
            self.validate_expr_ref(
                gb.expr,
                schema_cols,
                plan_aliases,
                errors,
                allow_derived=False,
            )

        for ob in plan.order_by:
            if ob.column.alias:
                 errors.append(
                    PipelineError(
                        node=node_name,
                        message=f"Aliases are only allowed in 'select_columns', not in order_by (found '{ob.column.alias}').",
                        severity=ErrorSeverity.WARNING,
                        error_code=ErrorCode.INVALID_ALIAS_USAGE,
                    )
                )
            self.validate_expr_ref(
                ob.column.expr,
                schema_cols,
                plan_aliases,
                errors,
                allow_derived=False,
            )

        for j in plan.joins:
            if j.left not in plan_tables and j.left not in plan_aliases:
                errors.append(
                    PipelineError(
                        node=node_name,
                        message=f"Join left '{j.left}' not declared in plan tables.",
                        severity=ErrorSeverity.WARNING,
                        error_code=ErrorCode.JOIN_TABLE_NOT_IN_PLAN,
                    )
                )
            if j.right not in plan_tables and j.right not in plan_aliases:
                errors.append(
                    PipelineError(
                        node=node_name,
                        message=f"Join right '{j.right}' not declared in plan tables.",
                        severity=ErrorSeverity.WARNING,
                        error_code=ErrorCode.JOIN_TABLE_NOT_IN_PLAN,
                    )
                )
            if not j.on:
                errors.append(
                    PipelineError(
                        node=node_name,
                        message=f"Join between '{j.left}' and '{j.right}' missing ON clause.",
                        severity=ErrorSeverity.WARNING,
                        error_code=ErrorCode.JOIN_MISSING_ON_CLAUSE,
                    )
                )

        self.validate_aggregations(plan, errors)

        if state.selected_datasource_id:
            self.validate_data_types(
                plan,
                state.relevant_tables,
                state.selected_datasource_id,
                errors,
            )

        return errors

    def _validate_semantic_correctness(self, state: GraphState) -> list[PipelineError]:
        """
        Layer 2: Semantic checks (Dry Run against DB).
        """
        errors = []
        if hasattr(state, "generated_sql") and state.generated_sql:
             try:
                 adapter = self.registry.get_adapter(state.selected_datasource_id)
                 dry_result = adapter.dry_run(state.generated_sql)
                 if not dry_result.is_valid:
                     errors.append(
                         PipelineError(
                             node="validator",
                             message=f"Dry Run Failed: {dry_result.error_message}",
                             severity=ErrorSeverity.ERROR,
                             error_code=ErrorCode.EXECUTION_ERROR
                         )
                     )
             except Exception as e:
                 logger.warning(f"Dry run skipped or failed: {e}")
        return errors

    def _validate_performance(self, state: GraphState) -> list[PipelineError]:
        """
        Layer 3: Performance checks (Cost Estimate & Explain).
        """
        errors = []
        if hasattr(state, "generated_sql") and state.generated_sql:
             try:
                 adapter = self.registry.get_adapter(state.selected_datasource_id)
                 
                 # Cost Check
                 cost = adapter.cost_estimate(state.generated_sql)
                 if cost.estimated_rows > 1_000_000:
                     # Fetch Explain plan for context
                     plan = adapter.explain(state.generated_sql)
                     plan_snippet = plan.plan_text[:500] + "..." if len(plan.plan_text) > 500 else plan.plan_text
                     
                     errors.append(
                         PipelineError(
                             node="validator",
                             message=f"Performance Warning: Query estimated to scan {cost.estimated_rows} rows (> 1M limit). Plan: {plan_snippet}",
                             severity=ErrorSeverity.WARNING, # Soft fail
                             error_code=ErrorCode.PERFORMANCE_WARNING
                         )
                     )
             except Exception as e:
                 logger.warning(f"Performance check skipped or failed: {e}")
        return errors

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        node_name = "validator"
        errors: list[PipelineError] = []

        try:
            if not state.plan:
                errors.append(
                    PipelineError(
                        node=node_name,
                        message="No plan to validate.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.MISSING_PLAN,
                    )
                )
                return {"errors": errors}

            if not state.relevant_tables:
                return {}

            plan = PlanModel(**state.plan)

            static_errors = self._validate_static_analysis(state, plan)
            errors.extend(static_errors)
            
            policy_errors = self._validate_policy(state, plan)
            errors.extend(policy_errors)

            has_blocking_errors = any(e.severity in (ErrorSeverity.CRITICAL, ErrorSeverity.ERROR) for e in errors)
            
            if not has_blocking_errors:
                semantic_errors = self._validate_semantic_correctness(state)
                errors.extend(semantic_errors)
                
                has_blocking_errors = any(e.severity in (ErrorSeverity.CRITICAL, ErrorSeverity.ERROR) for e in errors)

                if not has_blocking_errors:
                    perf_errors = self._validate_performance(state)
                    errors.extend(perf_errors)

            if errors:
                return {
                    "errors": errors,
                    "reasoning": [
                        {
                            "node": node_name,
                            "content": [e.message for e in errors],
                        }
                    ],
                }

            return {
                "errors": [],
                "reasoning": [
                    {
                        "node": node_name,
                        "content": "Validation successful. Plan is Valid, Safe, and Optimized.",
                    }
                ],
            }

        except Exception as exc:
            logger.error(exc)
            return {
                "errors": [
                    PipelineError(
                        node=node_name,
                        message=f"Validator crash: {exc}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.VALIDATOR_CRASH,
                        stack_trace=str(exc),
                    )
                ]
            }
