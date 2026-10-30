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


def ambiguous_columns(
    operation: str,
    inputs: Sequence[Sequence[str]],
    join_keys: Iterable[Dict[str, Any]],
) -> List[str]:
    """Right-hand non-key columns a ``join``'s left side also has (the ones suffixed ``_right``).

    After the combine the left copy keeps the bare name and the right one
    becomes ``<name>_right``, so a post-combine op reading the bare name reads
    the left copy only, whatever the model meant. A ``compare`` is exempt: it
    relies on matching names to diff the two sides.
    """
    if operation != "join" or len(inputs) < 2:
        return []
    left, right = list(inputs[0]), list(inputs[1])
    right_keys = {resolve_key(right, (k or {}).get("right") or "") for k in join_keys}
    return [c for c in right if c in left and c not in right_keys]


def ambiguous_columns_message(column: str, left_id: str, right_id: str, join_on: Sequence[str]) -> str:
    """Why a post-combine op cannot read ``column``, which both joined sub-queries produce.

    ``left_id`` and ``right_id`` describe the two sides (their intents).
    """
    return (
        f"'{column}' is on both sides of the join on {', '.join(join_on) or 'its keys'} "
        f"(left: \"{left_id}\", right: \"{right_id}\"): the left one keeps the name and the right "
        f"one becomes '{column}{RIGHT_SUFFIX}', so a post-combine operation reading '{column}' would "
        f"silently read only the left one. Give the sub-queries distinct names for it (for example "
        f"'left_{column}' and 'right_{column}', better ones that say what each measures), renamed in "
        f"each sub-query's expected_schema, so the operation reads the one you mean."
    )


def post_op_columns(operation: str, attributes: Dict[str, Any]) -> List[str]:
    """Every column a post-combine op reads from the combined result.

    Its aggregate inputs, filters and sort; not its ``expected_schema``, which
    is what the op ends in (:func:`post_op_schema`), after those have run.
    """
    return [name for name in (
        *((g or {}).get("attribute") for g in attributes.get("group_by") or []),
        *((m or {}).get("name") for m in attributes.get("metrics") or []),
        *((f or {}).get("attribute") for f in attributes.get("filters") or []),
        *((o or {}).get("attribute") for o in attributes.get("order_by") or []),
    ) if name]


def post_op_schema(attributes: Dict[str, Any]) -> List[str]:
    """The column names a post-combine op ends in, in order; empty keeps whatever it produced."""
    return [name for name in ((c or {}).get("name") for c in attributes.get("expected_schema") or []) if name]


def post_op_outputs(operation: str, attributes: Dict[str, Any], available: Sequence[str]) -> List[str]:
    """The columns a post-combine op can end in, given the ``available`` columns it reads.

    An ``aggregate`` leaves its group_by and metric names; any other op keeps
    every column it was given. A metric without an aggregation is not
    computed, so it is not an output.
    """
    if operation != "aggregate":
        return list(available)
    return [name for name in (
        *((g or {}).get("attribute") for g in attributes.get("group_by") or []),
        *((m or {}).get("name") for m in attributes.get("metrics") or [] if (m or {}).get("aggregation")),
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
