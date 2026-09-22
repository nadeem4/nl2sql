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
