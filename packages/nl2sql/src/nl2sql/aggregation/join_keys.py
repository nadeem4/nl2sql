"""How the declared types of a join key meet (docs/architecture/wave_a.md, D30).

The decomposer declares each join key's ``dtype`` on both sides; the engine's
aggregator casts both sides to one type before the join; the logical validator
rejects a column whose type cannot be coerced to the declared one. All three
read these rules, so they cannot drift apart. Plain Python, no polars and no
dialect: the validator puts a column's database type into a category itself.
"""
from __future__ import annotations

NUMBER, TEXT, TEMPORAL, BOOLEAN, OTHER = "number", "text", "temporal", "boolean", "other"

# The decomposer's ``ExpectedColumn.dtype`` values.
DTYPE_CATEGORY = {"int": NUMBER, "float": NUMBER, "string": TEXT, "date": TEMPORAL, "datetime": TEMPORAL,
                  "bool": BOOLEAN}

# Pairs no cast can join: a number or a boolean is never a date.
_NEVER = {frozenset({NUMBER, TEMPORAL}), frozenset({BOOLEAN, TEMPORAL})}

# How a message names a declared type, as one value and as many.
DTYPE_NOUN = {"int": "a number", "float": "a number", "string": "text", "date": "a date",
              "datetime": "a date and time", "bool": "a boolean"}
DTYPE_PLURAL = {"int": "numbers", "float": "numbers", "string": "text", "date": "dates",
                "datetime": "dates and times", "bool": "booleans"}
CATEGORY_NOUN = {NUMBER: "a number", TEXT: "text", TEMPORAL: "a date or time", BOOLEAN: "a boolean"}


def coercible(a: str, b: str) -> bool:
    """Whether values of category ``a`` can be coerced to category ``b`` (either way).

    ``other`` (a type nobody could place) is never refused: it is not checked.
    """
    return frozenset({a, b}) not in _NEVER


def join_dtype(left: str, right: str) -> str:
    """The one type both sides of a join key are cast to.

    Text is parsed as the other side's type; a whole number and a decimal meet
    as a decimal, a date and a timestamp as a timestamp, a number and a boolean
    as a boolean (0 and 1).
    """
    if left == right:
        return left
    categories = {DTYPE_CATEGORY[left], DTYPE_CATEGORY[right]}
    if categories == {NUMBER}:
        return "float"
    if categories == {TEMPORAL}:
        return "datetime"
    if DTYPE_CATEGORY[left] == TEXT:
        return right
    if DTYPE_CATEGORY[right] == TEXT:
        return left
    return "bool"

