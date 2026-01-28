from __future__ import annotations

from typing import Any, Dict, List, Tuple

import duckdb
import polars as pl

from nl2sql.execution.contracts import ArtifactRef
from nl2sql.execution.artifacts import build_artifact_store

from .base import AggregationEngine


class PolarsDuckdbEngine(AggregationEngine):
    def __init__(self):
        self.artifact_store = build_artifact_store()

    def load_scan(self, artifact: ArtifactRef) -> pl.DataFrame:
        if artifact.uri.startswith("s3://") or artifact.uri.startswith("abfs://"):
            result_frame = self.artifact_store.read_result_frame(artifact)
            return pl.from_dicts(result_frame.to_row_dicts())

        relation = duckdb.query(f"SELECT * FROM '{artifact.uri}'")
        table = relation.arrow()
        return pl.from_arrow(table)

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
            left_on = [k.get("left") for k in join_keys]
            right_on = [k.get("right") for k in join_keys]
            return left.join(right, left_on=left_on, right_on=right_on, how="inner", suffix="_right")
        if operation == "compare":
            if len(frames) < 2:
                return frames[0]
            left = frames[0]
            right = frames[1]
            left_on = [k.get("left") for k in join_keys]
            right_on = [k.get("right") for k in join_keys]
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
        if operation == "filter":
            rows = frame
            for flt in attributes.get("filters", []):
                attr = flt.get("attribute")
                op = flt.get("operator")
                val = flt.get("value")
                col = pl.col(attr)
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
        if operation == "aggregate":
            group_by = [g.get("attribute") for g in attributes.get("group_by", []) if g.get("attribute")]
            metrics = attributes.get("metrics", [])
            agg_exprs = []
            for metric in metrics:
                name = metric.get("name")
                agg = metric.get("aggregation")
                col = pl.col(name)
                if agg == "count":
                    agg_exprs.append(col.count().alias(name))
                elif agg == "sum":
                    agg_exprs.append(col.sum().alias(name))
                elif agg == "avg":
                    agg_exprs.append(col.mean().alias(name))
                elif agg == "min":
                    agg_exprs.append(col.min().alias(name))
                elif agg == "max":
                    agg_exprs.append(col.max().alias(name))
            if group_by:
                return frame.groupby(group_by).agg(agg_exprs)
            return frame.select(agg_exprs)
        if operation == "project":
            columns = [c.get("name") for c in attributes.get("expected_schema", []) if c.get("name")]
            return frame.select(columns) if columns else frame
        if operation == "sort":
            order_by = attributes.get("order_by", [])
            sort_cols = [o.get("attribute") for o in order_by if o.get("attribute")]
            descending = [o.get("direction") == "desc" for o in order_by if o.get("attribute")]
            if sort_cols:
                return frame.sort(sort_cols, descending=descending)
            return frame
        if operation == "limit":
            limit = attributes.get("limit")
            return frame.head(limit) if limit is not None else frame
        raise ValueError(f"Unsupported post-combine operation '{operation}'.")
