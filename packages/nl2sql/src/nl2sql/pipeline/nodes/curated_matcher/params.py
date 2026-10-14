"""The values a model chose for a verified query, checked by code.

The model returns each value as text. Here it is parsed as its declared type,
checked against ``min``/``max`` and ``choices``, and a parameter left out takes
its default. Anything that does not hold is a :class:`ParamError`, and the run
falls back to the generated pipeline. The values are bound by the adapter,
never written into the SQL.
"""
from __future__ import annotations

import math
import re
from datetime import date
from typing import Any, Dict, List

from nl2sql.config.curated import ParamSpec, VerifiedQuery

from .schemas import ParamValue

_INT = re.compile(r"^[+-]?\d+$")


class ParamError(ValueError):
    """A value the query cannot run with. ``reason`` is the matcher's fallback code."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def _parse(name: str, spec: ParamSpec, text: str) -> Any:
    """``text`` as ``spec.type``, checked against its bounds and choices."""
    if spec.type == "int":
        if not _INT.match(text):
            raise ParamError("param_invalid", f"{name}: {text!r} is not an int")
        value: Any = int(text)
    elif spec.type == "float":
        try:
            value = float(text)
        except ValueError:
            raise ParamError("param_invalid", f"{name}: {text!r} is not a number") from None
        if not math.isfinite(value):
            raise ParamError("param_invalid", f"{name}: {text!r} is not a finite number")
    elif spec.type == "date":
        try:
            value = date.fromisoformat(text)
        except ValueError:
            raise ParamError("param_invalid", f"{name}: {text!r} is not a date (YYYY-MM-DD)") from None
    else:
        value = text

    bound = (lambda v: date.fromisoformat(v)) if spec.type == "date" else (lambda v: v)
    if spec.min is not None and value < bound(spec.min):
        raise ParamError("param_invalid", f"{name}: {text!r} is below min {spec.min!r}")
    if spec.max is not None and value > bound(spec.max):
        raise ParamError("param_invalid", f"{name}: {text!r} is above max {spec.max!r}")
    if spec.choices is not None and value not in spec.choices:
        raise ParamError("param_invalid", f"{name}: {text!r} is not one of {spec.choices}")
    # A date is bound as ISO text, which every built-in driver compares as a date.
    return value.isoformat() if isinstance(value, date) else value


def resolve_params(query: VerifiedQuery, values: List[ParamValue]) -> Dict[str, Any]:
    """The values to bind for ``query``: every declared param, typed and checked.

    Raises:
        ParamError: ``param_invalid`` for an unknown name, a name given twice,
            a value that does not parse or is out of bounds; ``param_missing``
            for a param with no default that the model left out or empty.
    """
    given: Dict[str, str] = {}
    for item in values:
        if item.name not in query.params:
            raise ParamError("param_invalid", f"{item.name!r} is not a parameter of '{query.id}'")
        if item.name in given:
            raise ParamError("param_invalid", f"{item.name!r} was given twice")
        given[item.name] = item.value.strip()

    resolved: Dict[str, Any] = {}
    for name, spec in query.params.items():
        text = given.get(name, "")
        if text:
            resolved[name] = _parse(name, spec, text)
        elif spec.default is not None:
            resolved[name] = spec.default
        else:
            raise ParamError("param_missing", f"{name}: the question gives no value and it has no default")
    return resolved
