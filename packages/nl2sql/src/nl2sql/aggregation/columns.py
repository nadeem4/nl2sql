"""Which columns a combine produces, and which a post-combine op reads.

The aggregation engine combines sub-query results and runs post-combine ops on
them; a post-combine op that reads a column the combine does not produce fails
there, after every sub-query has run, and nothing upstream can retry it. The
decomposer asks these same functions first, from the decomposition alone, so
such a plan is rejected while the model can still be asked again.

A sub-query's result columns are its ``expected_schema`` names: the logical
validator rejects a plan whose select aliases differ from them.

Plain Python, no polars: :class:`~nl2sql.aggregation.engines.polars_duckdb.PolarsDuckdbEngine`
uses the same functions, so the prediction and the engine cannot drift apart.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# The order a combine's inputs are given to the engine: left-hand roles first.
_ROLE_RANK = {"left": 0, "base": 0, "primary": 0, "right": 1, "compare": 1, "secondary": 1}

_SIDE_PREFIXES = ("left", "right", "base", "compare", "primary", "secondary")

# What the engine appends to a right-hand column whose name the left side has.
RIGHT_SUFFIX = "_right"


def input_order(role: Optional[str], source_id: str) -> Tuple[int, str]:
    """Sort key for a combine's inputs, as ``(role, source id)``."""
    return _ROLE_RANK.get(role or "", 2), source_id


def resolve_key(columns: Sequence[str], key: str) -> str:
    """A join key as one of ``columns``.

    Models write keys qualified by side or sub-query (``right.customer``,
    ``sq_2.customer``); results have plain column names. An unknown key is
    returned unchanged.
    """
    if key in columns or not key or "." not in key:
        return key
    bare = key.rsplit(".", 1)[1]
    return bare if bare in columns else key


def combined_columns(
    operation: str,
    inputs: Sequence[Sequence[str]],
    join_keys: Iterable[Dict[str, Any]],
) -> List[str]:
    """The columns the engine's combine produces from inputs with these columns.

    ``inputs`` are in engine order (:func:`input_order`). A ``join`` or
    ``compare`` is an inner join of the first two on the join keys: the
    right-hand keys are dropped and any other right-hand column the left side
    also has is suffixed ``_right``.
    """
    if not inputs:
        return []
    left = list(inputs[0])
    if operation in ("standalone", "union") or len(inputs) < 2:
        return left
    right = list(inputs[1])
    right_keys = {resolve_key(right, (k or {}).get("right") or "") for k in join_keys}
    out = list(left)
    for column in right:
        if column in right_keys:
            continue
        out.append(column + RIGHT_SUFFIX if column in left else column)
    return out


def post_op_columns(operation: str, attributes: Dict[str, Any]) -> List[str]:
    """Every column a post-combine op reads from the combined result."""
    return [name for name in (
        *((g or {}).get("attribute") for g in attributes.get("group_by") or []),
        *((m or {}).get("name") for m in attributes.get("metrics") or []),
        *((c or {}).get("name") for c in attributes.get("expected_schema") or []
          if operation == "project"),
        *((f or {}).get("attribute") for f in attributes.get("filters") or []),
        *((o or {}).get("attribute") for o in attributes.get("order_by") or []),
    ) if name]


def missing_columns_message(missing: Sequence[str], available: Sequence[str]) -> str:
    """Why a post-combine op that reads ``missing`` cannot run on ``available``.

    A side-qualified name (``right.customer``) is almost always one question:
    "which customers bought jazz but never rock?". The plan language has no
    anti-join -- every two-input combine is an inner join -- so the model
    reaches for a right-hand column to negate against, and that column does not
    survive the combine. Resolving the prefix away would filter the
    *intersection* and answer "bought both", silently wrong.
    """
    detail = (
        f"Post-combine operation references {', '.join(repr(m) for m in missing)}, "
        f"which the combined result does not have. Available columns: {', '.join(available) or 'none'}."
    )
    if any("." in m and m.split(".", 1)[0].lower() in _SIDE_PREFIXES for m in missing):
        detail += (
            " A column qualified by side does not survive the combine: joining on"
            " the shared column drops the right-hand copy, and the other right-hand"
            " columns are suffixed '_right'. This is what a question of the form"
            " 'has X but never Y' looks like here, and it cannot be expressed:"
            " the plan language has no anti-join or set-difference operation"
            " (combine is one of standalone, compare, join, union -- all inner),"
            " so the question is refused rather than answered with the"
            " intersection."
        )
    return detail
