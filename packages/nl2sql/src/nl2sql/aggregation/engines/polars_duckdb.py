from __future__ import annotations

from typing import Any, Dict, List, Tuple

import duckdb
import polars as pl

from nl2sql.execution.contracts import ArtifactRef
from nl2sql.execution.artifacts import build_artifact_store


def _column(frame: pl.DataFrame, key: str) -> str:
    """A join key as a column of ``frame``.

    Models write keys qualified by side or sub-query (``right.customer``,
    ``sq_2.customer``); the frames have plain column names. An unknown key is
    returned unchanged, so polars reports it.
    """
    if key in frame.columns or not key or "." not in key:
        return key
    bare = key.rsplit(".", 1)[1]
    return bare if bare in frame.columns else key


_SIDE_PREFIXES = ("left", "right", "base", "compare", "primary", "secondary")


def _check_attributes(frame: pl.DataFrame, names: List[str]) -> None:
    """Refuses a post-combine attribute the combined frame does not have.

    polars reports this as ``unable to find column "right.customer"; valid
    columns: ["customer"]``, which tells neither the caller nor the planner
    anything. It is almost always one question: "which customers bought jazz
    but never rock?".

    The plan language has no anti-join. ``CombineGroup.operation`` is one of
    ``standalone``, ``compare``, ``join`` or ``union``, and both two-input
    operations are **inner** joins; ``FilterSpec`` has no null or existence
    operator either. So there is no way to say "present on the left and absent
    on the right", and the model reaches for a right-hand column to negate
    against instead. That column does not exist: an inner join keyed on the
    shared column drops the right-hand copy and suffixes the rest with
    ``_right``.

    Resolving the prefix away would be worse than the crash. ``right.customer``
    would become ``customer``, the filter would run against the *intersection*,
    and the answer to "bought jazz but never rock" would be the customers who
    bought both -- wrong, and silently so. The question is refused with the
    reason instead.
    """
    missing = [name for name in names if name and name not in frame.columns]
    if not missing:
        return

    available = ", ".join(frame.columns) or "none"
    detail = (
        f"Post-combine operation references {', '.join(repr(m) for m in missing)}, "
        f"which the combined result does not have. Available columns: {available}."
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
    raise ValueError(detail)


def _filter(rows: pl.DataFrame, filters: List[Dict[str, Any]]) -> pl.DataFrame:
    for flt in filters:
        col, op, val = pl.col(flt.get("attribute")), flt.get("operator"), flt.get("value")
        if op == "=":
            rows = rows.filter(col == val)
        elif op == "!=":
            rows = rows.filter(col != val)
        elif op == ">":
            rows = rows.filter(col > val)
        elif op == ">=":
            rows = rows.filter(col >= val)
        elif op == "<":
            rows = rows.filter(col < val)
        elif op == "<=":
            rows = rows.filter(col <= val)
        elif op == "between" and isinstance(val, list) and len(val) == 2:
            rows = rows.filter((col >= val[0]) & (col <= val[1]))
        elif op == "in" and isinstance(val, list):
            rows = rows.filter(col.is_in(val))
        elif op == "contains":
            rows = rows.filter(col.cast(pl.Utf8).str.contains(str(val)))
    return rows


_AGGREGATIONS = {
    "count": lambda c: c.count(),
    "sum": lambda c: c.sum(),
    "avg": lambda c: c.mean(),
    "min": lambda c: c.min(),
    "max": lambda c: c.max(),
}


def _aggregate(frame: pl.DataFrame, attributes: Dict[str, Any]) -> pl.DataFrame:
    group_by = [g.get("attribute") for g in attributes.get("group_by", []) if g.get("attribute")]
    exprs = [
        _AGGREGATIONS[m.get("aggregation")](pl.col(m.get("name"))).alias(m.get("name"))
        for m in attributes.get("metrics", [])
        if m.get("aggregation") in _AGGREGATIONS
    ]
    if group_by:
        # polars 1.x: group_by (groupby was removed).
        return frame.group_by(group_by, maintain_order=True).agg(exprs)
    return frame.select(exprs)


class PolarsDuckdbEngine:
    def __init__(self):
        self.artifact_store = build_artifact_store()

    def load_scan(self, artifact: ArtifactRef) -> pl.DataFrame:
        return self.artifact_store.read_parquet(artifact)

    def combine(
        self,
        operation: str,
        inputs: List[Tuple[str, pl.DataFrame]],
        join_keys: List[Dict[str, Any]],
    ) -> pl.DataFrame:
        frames = [frame for _, frame in inputs]
        if not frames:
            return pl.DataFrame()
        if operation == "standalone":
            return frames[0]
        if operation == "union":
            return pl.concat(frames, how="vertical")
        if operation == "join":
            if len(frames) < 2:
                return frames[0]
            left = frames[0]
            right = frames[1]
            left_on = [_column(left, k.get("left")) for k in join_keys]
            right_on = [_column(right, k.get("right")) for k in join_keys]
            return left.join(right, left_on=left_on, right_on=right_on, how="inner", suffix="_right")
        if operation == "compare":
            if len(frames) < 2:
                return frames[0]
            left = frames[0]
            right = frames[1]
            left_on = [_column(left, k.get("left")) for k in join_keys]
            right_on = [_column(right, k.get("right")) for k in join_keys]
            joined = left.join(right, left_on=left_on, right_on=right_on, how="inner", suffix="_right")
            diff_cols = []
            for col in left.columns:
                if col in left_on:
                    continue
                right_col = f"{col}_right"
                if right_col in joined.columns:
                    diff_cols.append((col, right_col))
            if not diff_cols:
                return joined
            diff_exprs = [(pl.col(l) != pl.col(r)) for l, r in diff_cols]
            return joined.filter(pl.any_horizontal(diff_exprs))
        raise ValueError(f"Unsupported combine operation '{operation}'.")

    def post_op(self, operation: str, frame: pl.DataFrame, attributes: Dict[str, Any]) -> pl.DataFrame:
        """Applies one post-combine op, every field it carries, in SQL's order.

        ``operation`` picks the reshaping step (``aggregate`` or ``project``);
        whatever the operation, the op's filters, order_by and limit are then
        applied, in that order. A filter after an aggregate filters the
        aggregated rows, as HAVING does. The decomposer's own example is a
        ``filter`` op carrying ``order_by`` and ``limit``, so applying only the
        named field dropped them.
        """
        if operation not in {"filter", "aggregate", "project", "sort", "limit"}:
            raise ValueError(f"Unsupported post-combine operation '{operation}'.")
        # Every column this op will read, checked against the combined frame
        # before polars is asked for any of them. See `_check_attributes`.
        _check_attributes(frame, [
            *(g.get("attribute") for g in attributes.get("group_by", [])),
            *(m.get("name") for m in attributes.get("metrics", [])),
            *(c.get("name") for c in attributes.get("expected_schema", [])
              if operation == "project"),
            *(f.get("attribute") for f in attributes.get("filters", [])),
            *(o.get("attribute") for o in attributes.get("order_by", [])),
        ])
        rows = frame
        if operation == "aggregate":
            rows = _aggregate(rows, attributes)
        elif operation == "project":
            columns = [c.get("name") for c in attributes.get("expected_schema", []) if c.get("name")]
            rows = rows.select(columns) if columns else rows
        rows = _filter(rows, attributes.get("filters", []))
        order_by = [o for o in attributes.get("order_by", []) if o.get("attribute")]
        if order_by:
            rows = rows.sort([o["attribute"] for o in order_by],
                             descending=[o.get("direction") == "desc" for o in order_by])
        limit = attributes.get("limit")
        return rows.head(limit) if limit is not None else rows

    def to_rows(self, frame: pl.DataFrame) -> List[Dict[str, Any]]:
        return frame.to_dicts()
