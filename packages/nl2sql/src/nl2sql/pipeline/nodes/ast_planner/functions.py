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


# Portable date operations. A plan writes DATE_PART(unit, date), an integer,
# and DATE_TRUNC(unit, date), an ISO 'YYYY-MM-DD' string, the same way for
# every database; the unit is a string literal. YEAR(x), QUARTER(x), MONTH(x),
# DAY(x) and EXTRACT(unit, x) are read as DATE_PART.
DATE_UNITS = ("year", "quarter", "month", "day")
_DATE_PART = {"DATE_PART", "EXTRACT"}
_DATE_TRUNC = {"DATE_TRUNC"}
_UNIT_FUNCTIONS = {"YEAR": "year", "QUARTER": "quarter", "MONTH": "month", "DAY": "day"}


def date_operation(func_name: str | None, args: list) -> tuple | None:
    """Reads a date function as ``(kind, unit, operand)``, or None for any other function.

    ``kind`` is ``"part"`` or ``"trunc"``. ``unit`` is lower-cased and may be
    outside ``DATE_UNITS``; ``unit`` and ``operand`` are None when the call is
    malformed. The unit may come first or second: it is the string literal.
    """
    name = (func_name or "").strip().upper()
    if name in _UNIT_FUNCTIONS:
        return ("part", _UNIT_FUNCTIONS[name], args[0]) if len(args) == 1 else ("part", None, None)
    if name in _DATE_PART:
        kind = "part"
    elif name in _DATE_TRUNC:
        kind = "trunc"
    else:
        return None
    if len(args) != 2:
        return kind, None, None
    units = [a for a in args if a.kind == "literal" and isinstance(a.value, str)]
    if len(units) != 1:
        return kind, None, None
    operand = args[1] if args[0] is units[0] else args[0]
    return kind, units[0].value.strip().lower(), operand


def is_allowed_function(name: str | None) -> bool:
    """Whether ``name`` is a plain identifier naming an allowed function."""
    return bool(name) and bool(FUNCTION_NAME.match(name)) and name.upper() in ALLOWED_FUNCTIONS
