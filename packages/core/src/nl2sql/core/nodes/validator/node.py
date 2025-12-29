from __future__ import annotations

import json
from typing import Set, Dict, Any
from datetime import datetime

from nl2sql.core.schemas import GraphState
from nl2sql.core.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.core.nodes.planner.schemas import PlanModel
from nl2sql.core.nodes.schema.schemas import SchemaInfo
from nl2sql.core.datasource_registry import DatasourceRegistry
from nl2sql.core.logger import get_logger

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
        schema_info: SchemaInfo,
        profile_id: str,
        errors: list[PipelineError],
    ) -> None:
        try:
            profile = self.registry.get_profile(profile_id)
            date_format = profile.date_format or "ISO 8601"
        except Exception:
            date_format = "ISO 8601"

        col_types: Dict[str, str] = {}
        for table in schema_info.tables:
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

            if not state.schema_info:
                return {}

            plan = PlanModel(**state.plan)

            state_entity_ids = []
            if getattr(state, "entity_ids", None):
                state_entity_ids = list(state.entity_ids)
            elif getattr(state, "entities", None):
                state_entity_ids = [e.entity_id for e in state.entities]

            if state_entity_ids:
                missing = [e for e in state_entity_ids if e not in plan.entity_ids]
                if missing:
                    errors.append(
                        PipelineError(
                            node=node_name,
                            message=f"Plan missing required entity_ids={missing}.",
                            severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.INVALID_PLAN_STRUCTURE,
                        )
                    )

            if plan.query_type != "READ":
                errors.append(
                    PipelineError(
                        node=node_name,
                        message=f"Query type '{plan.query_type}' not allowed.",
                        severity=ErrorSeverity.CRITICAL,
                        error_code=ErrorCode.SECURITY_VIOLATION,
                    )
                )
                return {"errors": errors}

            schema_cols: Set[str] = set()
            plan_aliases: Set[str] = set()
            plan_tables: Set[str] = set()

            for t in plan.tables:
                plan_aliases.add(t.alias)
                plan_tables.add(t.name)

                found = False
                for st in state.schema_info.tables:
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
                # Group By expressions usually shouldn't define new aliases, but referencing select aliases is DB dependent.
                # Here we assume strict: no defining aliases.
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
                    state.schema_info,
                    state.selected_datasource_id,
                    errors,
                )

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
                        "content": "Validation successful. Plan is schema-safe and entity-complete.",
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
