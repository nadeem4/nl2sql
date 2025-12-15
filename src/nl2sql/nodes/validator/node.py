from __future__ import annotations

import json
from typing import Callable, Set, Dict, TYPE_CHECKING, Any
from datetime import datetime

if TYPE_CHECKING:
    from nl2sql.schemas import GraphState

from nl2sql.nodes.planner.schemas import ColumnRef, PlanModel
from nl2sql.nodes.schema.schemas import SchemaInfo
from nl2sql.datasource_registry import DatasourceRegistry

from nl2sql.logger import get_logger

logger = get_logger("validator")

LLMCallable = Callable[[str], str]


class ValidatorNode:
    """
    Validates the generated execution plan against the schema and security policies.

    Ensures that:
    - The query type is allowed (READ only).
    - Tables and columns exist in the schema.
    - Aliases are used correctly.
    - No forbidden operations are present.
    - Data types and formats are correct.
    """

    def __init__(self, registry: DatasourceRegistry, row_limit: int | None = None):
        """
        Initializes the ValidatorNode.

        Args:
            registry: Datasource registry for accessing profiles.
            row_limit: Optional maximum number of rows allowed (not strictly enforced here but good context).
        """
        self.registry = registry
        self.row_limit = row_limit

    def is_aggregation(self, expr: str) -> bool:
        """Checks if an expression looks like an aggregation."""
        upper_expr = expr.upper()
        aggs = ["COUNT(", "SUM(", "AVG(", "MIN(", "MAX("]
        return any(agg in upper_expr for agg in aggs)
    
    def validate_data_types(self, plan: PlanModel, schema_info: SchemaInfo, profile_id: str, errors: list[str]) -> None:
        """
        Validates that literals in filters match the column types and date formats.
        """
        try:
            profile = self.registry.get_profile(profile_id)
            date_format = profile.date_format or "ISO 8601"
        except ValueError:
            date_format = "ISO 8601"

        # Build map of column expression -> type
        col_types = {}
        for table in schema_info.tables:
             for col in table.columns:
                 col_types[col.name] = col.type # e.g. "t1.id" -> "INTEGER"

        def check_value(col_expr: str, value: Any, op: str):
            if col_expr not in col_types:
                return 
            
            sql_type = col_types[col_expr].upper()
            
            # Date/Time Validation
            if "DATE" in sql_type or "TIME" in sql_type:
                if isinstance(value, str):
                    # Remove quotes
                    val_str = value.strip("'").strip('"')
                    
                    # Special check for ISO 8601
                    if "ISO 8601" in date_format:
                         # Try generic ISO formats
                         formats = ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S"]
                         valid = False
                         for fmt in formats:
                             try:
                                 datetime.strptime(val_str, fmt)
                                 valid = True
                                 break
                             except ValueError:
                                 continue
                         if not valid:
                             errors.append(f"Invalid date format for '{col_expr}' ({sql_type}). Value '{val_str}' does not match ISO 8601 (YYYY-MM-DD).")
                    else:
                        # Specific format check
                        try:
                            if "%" in date_format: 
                                datetime.strptime(val_str, date_format)
                        except ValueError:
                             errors.append(f"Invalid date format for '{col_expr}'. Value '{val_str}' does not match required format '{date_format}'.")
            
            # Numeric Validation
            elif "INT" in sql_type or "DECIMAL" in sql_type or "FLOAT" in sql_type:
                if isinstance(value, str) and not value.replace('.', '', 1).isdigit():
                     val_str = value.strip("'").strip('"')
                     if not val_str.replace('.', '', 1).isdigit():
                          errors.append(f"Invalid numeric value for '{col_expr}' ({sql_type}): {value}")

        for flt in plan.filters:
             check_value(flt.column.expr, flt.value, flt.op)
        
        for hav in plan.having:
             check_value(hav.expr, hav.value, hav.op)


    def validate_aggregations(self, plan: PlanModel, errors: list[str]) -> None:
        """
        Validates GROUP BY logic.
        
        Rules:
        1. If there are aggregations in SELECT, all non-aggregated columns must be in GROUP BY.
        2. If there is a GROUP BY, all non-aggregated columns in SELECT must be in GROUP BY.
        """
        has_aggregations = False
        non_aggregated_cols = []
        
        for col in plan.select_columns:
            if col.is_derived or self.is_aggregation(col.expr):
                has_aggregations = True
            else:
                non_aggregated_cols.append(col)
                
        group_by_exprs = {gb.expr for gb in plan.group_by}
        
        if has_aggregations or plan.group_by:
            for col in non_aggregated_cols:
                if col.expr not in group_by_exprs:
                    errors.append(f"Column '{col.expr}' is selected but not aggregated or included in GROUP BY.")

    def validate_column_ref(self, col: ColumnRef, schema_cols: Set[str], plan_table_aliases: Set[str], context: str, errors: list[str]) -> None:
        """
        Validates a column reference.

        Args:
            col: The ColumnRef object to validate.
            schema_cols: Set of valid column expressions from the schema (e.g., "t1.name").
            plan_table_aliases: Set of table aliases defined in the plan.
            context: The context in which the column is used (e.g., "select_columns").
            errors: List to append error messages to.
        """
        if col.alias and context != "select_columns":
            errors.append(f"Column alias '{col.alias}' used in '{context}'. Aliases are only allowed in 'select_columns'.")

        if col.is_derived:
            pass
        else:
            if col.expr not in schema_cols:
                errors.append(f"Column '{col.expr}' not found in schema. Ensure you are using the pre-aliased name (e.g., 't1.col').")

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """
        Executes the validation step.

        Args:
            state: The current graph state.

        Returns:
            Dictionary updates for the graph state with validation errors (if any).
        """
        node_name = "validator"

        try:
            # Clear errors from previous runs effectively by returning a new list
            errors = []

            if not state.plan:
                errors.append("No plan to validate.")
                return {"errors": errors}

            if not state.schema_info:
                return {} # No schema, skip validation

            query_type = state.plan.get("query_type", "READ")
            if query_type != "READ":
                errors.append(f"Security Violation: Query type '{query_type}' is not allowed. Only READ queries are permitted.")
                return {"errors": errors}

            try:
                plan = PlanModel(**state.plan)
            except Exception as e:
                errors.append(f"Invalid plan structure: {e}")
                return {"errors": errors}

            schema_cols = set()
            
            plan_table_aliases = set()
            plan_table_names = set()
            for t in plan.tables:
                plan_table_aliases.add(t.alias)
                plan_table_names.add(t.name)
                
                found = False
                for st in state.schema_info.tables:
                    if st.name == t.name and st.alias == t.alias:
                        # Extract names from ColumnInfo objects
                        for c in st.columns:
                             schema_cols.add(c.name)
                        found = True
                        break
                
                if not found:
                        errors.append(f"Table '{t.name}' with alias '{t.alias}' not found in schema or alias mismatch.")

            for col in plan.select_columns:
                self.validate_column_ref(col, schema_cols, plan_table_aliases, "select_columns", errors)

            for flt in plan.filters:
                self.validate_column_ref(flt.column, schema_cols, plan_table_aliases, "filters", errors)

            for gb in plan.group_by:
                self.validate_column_ref(gb, schema_cols, plan_table_aliases, "group_by", errors)

            for ob in plan.order_by:
                self.validate_column_ref(ob.column, schema_cols, plan_table_aliases, "order_by", errors)

            for join in plan.joins:
                if join.left not in plan_table_names:
                        errors.append(f"Join left table '{join.left}' is not in plan tables.")
                if join.right not in plan_table_names:
                        errors.append(f"Join right table '{join.right}' is not in plan tables.")
                
                if not join.on:
                    errors.append(f"Join between '{join.left}' and '{join.right}' has no ON clause.")

            for hav in plan.having:
                if not hav.expr:
                    errors.append(f"Having clause missing expression: {hav}")

            self.validate_aggregations(plan, errors)
            
            if state.selected_datasource_id:
                self.validate_data_types(plan, state.schema_info, state.selected_datasource_id, errors)

            validator_thoughts = []
            if errors:
                validator_thoughts.append("Validation Failed")
                for err in errors:
                    validator_thoughts.append(f"Error: {err}")
            else:
                validator_thoughts.append("Validation Successful")
                validator_thoughts.append("Plan is valid against schema and security policies.")
            
            
            return {
                "errors": errors,
                "reasoning": {"validator": validator_thoughts}
            }

        except Exception as exc:
            logger.error(f"Node {node_name} failed: {exc}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                "errors": [f"Validation exception: {exc}"],
                "reasoning": {"validator": [f"Exception: {exc}"]}
            }
