"""The functions a plan may call.

A ``func`` expression's ``func_name`` is text the model wrote, and it becomes
the function's name in the SQL. The logical validator accepts only a plain
identifier from ``ALLOWED_FUNCTIONS`` (in any case), so nothing else can reach
the SQL through it. Everything here is a read-only scalar or aggregate
function.
"""
from __future__ import annotations

import re

FUNCTION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

AGGREGATE_FUNCTIONS = frozenset({"COUNT", "SUM", "AVG", "MIN", "MAX"})

ALLOWED_FUNCTIONS = AGGREGATE_FUNCTIONS | frozenset({
    # NULL handling
    "COALESCE", "NULLIF", "IFNULL",
    # Numbers
    "ABS", "ROUND", "CEIL", "CEILING", "FLOOR", "POWER", "SQRT", "MOD", "SIGN", "GREATEST", "LEAST",
    # Strings
    "LOWER", "UPPER", "LENGTH", "SUBSTR", "SUBSTRING", "TRIM", "LTRIM", "RTRIM", "REPLACE", "CONCAT",
    # Dates
    "DATE", "DATETIME", "STRFTIME", "YEAR", "QUARTER", "MONTH", "DAY", "EXTRACT", "DATE_PART", "DATE_TRUNC",
    # The value list of an IN (built by the generator, never rendered as a call)
    "TUPLE", "LIST",
})


def is_allowed_function(name: str | None) -> bool:
    """Whether ``name`` is a plain identifier naming an allowed function."""
    return bool(name) and bool(FUNCTION_NAME.match(name)) and name.upper() in ALLOWED_FUNCTIONS
