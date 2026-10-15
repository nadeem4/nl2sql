"""The join-key type check (docs/architecture/wave_a.md, D30).

The aggregator casts both sides of a join key to one type, so almost any pair
joins. What cannot be coerced is a date against a number or a boolean: such a
plan is rejected here, with a retryable ``JOIN_KEY_TYPE_MISMATCH`` that asks
for another column. Never for a cast: the plan has none to offer.

A column's database type is put in a category by sqlglot, in the datasource's
own dialect (the adapter's ``get_dialect()``), so no dialect is named here. A
type sqlglot cannot place, a column with no type, and a computed select item
are not checked.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlglot import expressions as exp
from sqlglot.errors import SqlglotError

from nl2sql.aggregation.join_keys import (
    BOOLEAN, CATEGORY_NOUN, DTYPE_CATEGORY, DTYPE_NOUN, NUMBER, OTHER, TEMPORAL, TEXT, coercible,
)
from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel


def column_category(data_type: Optional[str], dialect: Optional[str]) -> str:
    """``number``, ``text``, ``temporal``, ``boolean`` or ``other`` for a database type name."""
    if not data_type:
        return OTHER
    try:
        built = exp.DataType.build(data_type, dialect=dialect)
    except (SqlglotError, ValueError):
        return OTHER
    if built.is_type(exp.DataType.Type.BOOLEAN):
        return BOOLEAN
    if built.is_type(*exp.DataType.NUMERIC_TYPES):
        return NUMBER
    if built.is_type(*exp.DataType.TEXT_TYPES):
        return TEXT
    if built.is_type(*exp.DataType.TEMPORAL_TYPES):
        return TEMPORAL
    return OTHER


def join_key_type_errors(plan: PlanModel, sub_query, relevant_tables, dialect: Optional[str]) -> List[PipelineError]:
    """A ``JOIN_KEY_TYPE_MISMATCH`` for each join-key select item whose column cannot take its declared type."""
    declared = {c.name: c.dtype for c in (sub_query.expected_schema if sub_query else [])
                if c.join_key and c.dtype}
    if not declared:
        return []
    tables = {t.alias: t.name for t in plan.tables}
    columns: Dict[tuple, Optional[str]] = {
        (table.name.lower().split(".")[-1], column.name.lower()): column.type
        for table in relevant_tables for column in table.columns or []
    }
    errors = []
    for item in plan.select_items:
        dtype = declared.get(item.alias or "")
        expr = item.expr
        if not dtype or expr.kind != "column" or not expr.column_name:
            continue
        table = tables.get(expr.alias or "")
        if table is None:
            continue
        category = column_category(columns.get((table.lower().split(".")[-1], expr.column_name.lower())), dialect)
        if category == OTHER or coercible(category, DTYPE_CATEGORY[dtype]):
            continue
        noun = DTYPE_NOUN[dtype]
        errors.append(PipelineError(
            node="logical_validator",
            message=(f"The join key {item.alias} is declared {noun}, but {table}.{expr.column_name} is "
                     f"{CATEGORY_NOUN[category]}. Select a column that holds {noun} for this key."),
            severity=ErrorSeverity.ERROR,
            error_code=ErrorCode.JOIN_KEY_TYPE_MISMATCH,
        ))
    return errors
