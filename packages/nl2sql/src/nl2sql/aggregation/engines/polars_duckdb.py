from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

import duckdb
import polars as pl

from nl2sql.aggregation.columns import missing_columns_message, post_op_columns, resolve_key
from nl2sql.aggregation.join_keys import DTYPE_CATEGORY, NUMBER, join_dtype
from nl2sql.execution.contracts import ArtifactRef
from nl2sql.execution.artifacts import ArtifactStore


# Every frame here is a polars LazyFrame, or a DataFrame a caller passed in:
# both have the operations used below, and nothing is collected until the
# aggregator writes the result.
Frame = Union[pl.DataFrame, pl.LazyFrame]


def _names(frame: Frame) -> List[str]:
    """The column names of ``frame``, without collecting a lazy one."""
    return frame.collect_schema().names()


def _column(frame: Frame, key: str) -> str:
    """A join key as a column of ``frame``; see :func:`~nl2sql.aggregation.columns.resolve_key`."""
    return resolve_key(_names(frame), key)


_POLARS_TYPES = {"int": pl.Int64, "float": pl.Float64, "string": pl.String, "bool": pl.Boolean,
                 "date": pl.Date, "datetime": pl.Datetime("us")}
_TRUE, _FALSE = ["true", "t", "yes", "y", "1"], ["false", "f", "no", "n", "0"]

# Text is parsed against these formats only, never by inference: inference
# fails on a column whose first value it cannot read, and ends the run.
_DATE_FORMATS = ["%Y-%m-%d"]
_NAIVE_FORMATS = ["%Y-%m-%d %H:%M:%S%.f", "%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M",
                  "%Y-%m-%dT%H:%M:%S%.fZ", "%Y-%m-%d %H:%M:%S%.fZ"]
_OFFSET_FORMATS = ["%Y-%m-%dT%H:%M:%S%.f%z", "%Y-%m-%d %H:%M:%S%.f%z"]


def _parse_date(text: pl.Expr) -> pl.Expr:
    return pl.coalesce(*(text.str.to_date(f, strict=False) for f in _DATE_FORMATS))


def _parse_datetime(text: pl.Expr) -> pl.Expr:
    """ISO text as a naive UTC timestamp in microseconds; null where no format fits."""
    naive = [text.str.to_datetime(f, time_unit="us", strict=False) for f in _NAIVE_FORMATS]
    zoned = [text.str.to_datetime(f, time_unit="us", time_zone="UTC", strict=False).dt.replace_time_zone(None)
             for f in _OFFSET_FORMATS]
    return pl.coalesce(*naive, *zoned, _parse_date(text).cast(pl.Datetime("us")))


def _normalised(value: pl.Expr, current: pl.DataType) -> Tuple[pl.Expr, pl.DataType]:
    """Text stripped; a timestamp of any unit or zone as naive UTC microseconds."""
    if current == pl.String:
        return value.str.strip_chars(), current
    if isinstance(current, pl.Datetime):
        if current.time_zone:
            value = value.dt.convert_time_zone("UTC").dt.replace_time_zone(None)
        return value.dt.cast_time_unit("us"), pl.Datetime("us")
    return value, current


def _cast(value: pl.Expr, current: pl.DataType, dtype: str) -> pl.Expr:
    """``value`` (of polars type ``current``) as ``dtype``; null where it does not convert.

    Never a changed value: a number that is not whole is not an integer (7.9
    is not 7), and text that matches none of the ISO formats is not a date.
    Null is what :meth:`PolarsDuckdbEngine.unconverted_join_keys` counts.
    """
    target = _POLARS_TYPES[dtype]
    value, current = _normalised(value, current)
    if current == target:
        return value
    if current == pl.Time and dtype in ("date", "datetime"):
        return pl.lit(None, dtype=target)
    if current == pl.String:
        if dtype == "bool":
            lower = value.str.to_lowercase()
            return pl.when(lower.is_in(_TRUE)).then(True).when(lower.is_in(_FALSE)).then(False)
        if dtype == "date":
            return _parse_date(value)
        if dtype == "datetime":
            return _parse_datetime(value)
        return value.cast(target, strict=False)
    if dtype == "bool" and current.is_numeric():
        return pl.when(value == 1).then(True).when(value == 0).then(False)
    if dtype == "int" and (current.is_float() or current.is_decimal()):
        return pl.when(value == value.round(0)).then(value.cast(pl.Int64, strict=False))
    if dtype == "date" and current == pl.Datetime("us"):
        return value.dt.date()
    return value.cast(target, strict=False)


def _coerced_key(frame: Frame, column: str, declared: str, target: str) -> pl.Expr:
    """A join key cast first to its side's declared dtype, then to the type both sides meet as.

    Through the declared type, so a value is read the way its sub-query said
    it is: a date declared as text joins a number key as its text, which is
    not a number, rather than as a count of days. A number goes straight to
    the number type the sides meet as, so an integer id never passes through
    a float (which cannot tell 2**53 from 2**53 + 1).
    """
    current = frame.collect_schema()[column]
    if DTYPE_CATEGORY[declared] == NUMBER == DTYPE_CATEGORY[target]:
        return _cast(pl.col(column), current, target)
    return _cast(_cast(pl.col(column), current, declared), _POLARS_TYPES[declared], target)


def _key_types(left: Frame, right: Frame, key: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    """``(left column, right column, joined dtype)`` for a typed key; None for an untyped one.

    Two keys declared ``float`` whose frames both hold integers meet as
    ``int``: a float join would match ids above 2**53 that differ.
    """
    left_dtype, right_dtype = key.get("left_dtype"), key.get("right_dtype")
    if not left_dtype or not right_dtype:
        return None
    left_column, right_column = _column(left, key.get("left")), _column(right, key.get("right"))
    target = join_dtype(left_dtype, right_dtype)
    if target == "float" and all(frame.collect_schema()[column].is_integer()
                                 for frame, column in ((left, left_column), (right, right_column))):
        target = "int"
    return left_column, right_column, target


def _coerce_keys(left: Frame, right: Frame, join_keys: List[Dict[str, Any]]) -> Tuple[Frame, Frame]:
    """Both sides of every typed join key cast to one type, in polars (wave_a.md, D30).

    A key the decomposer gave no dtypes (none reaches here from a run) is left
    as it is. A value that does not convert becomes null and matches nothing;
    :meth:`PolarsDuckdbEngine.unconverted_join_keys` counts those.
    """
    for key in join_keys:
        types = _key_types(left, right, key)
        if types is None:
            continue
        left_column, right_column, target = types
        left = left.with_columns(_coerced_key(left, left_column, key["left_dtype"], target).alias(left_column))
        right = right.with_columns(_coerced_key(right, right_column, key["right_dtype"], target).alias(right_column))
    return left, right


def _check_attributes(frame: Frame, names: List[str]) -> None:
    """Refuses a post-combine attribute the combined frame does not have.

    polars reports this as ``unable to find column "right.customer"; valid
    columns: ["customer"]``, which tells neither the caller nor the planner
    anything. The decomposer runs the same check on the decomposition before
    anything executes (``nl2sql.aggregation.columns``) and asks the model
    again, so this is the last line of defence; the message explains why a
    side-qualified column cannot work (see ``missing_columns_message``).
    """
    columns = _names(frame)
    missing = [name for name in names if name and name not in columns]
    if missing:
        raise ValueError(missing_columns_message(missing, columns))


def _filter(rows: Frame, filters: List[Dict[str, Any]]) -> Frame:
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


def _aggregate(frame: Frame, attributes: Dict[str, Any]) -> Frame:
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
    def __init__(self, artifact_store: ArtifactStore):
        self.artifact_store = artifact_store

    def load_scan(self, artifact: ArtifactRef) -> pl.LazyFrame:
        """A sub-query's artifact as a polars scan: read only when the result is written."""
        return self.artifact_store.scan_parquet(artifact)

    def combine(
        self,
        operation: str,
        inputs: List[Tuple[str, Frame]],
        join_keys: List[Dict[str, Any]],
    ) -> Frame:
        frames = [frame for _, frame in inputs]
        if not frames:
            return pl.LazyFrame()
        if operation == "standalone":
            return frames[0]
        if operation == "union":
            return pl.concat(frames, how="vertical")
        if operation in ("join", "compare") and len(frames) < 2:
            # The decomposer refuses such a group before anything runs; this is
            # the last line of defence. The left frame alone is not the join.
            raise ValueError(
                f"A {operation} needs two inputs and got {len(frames)}; refusing to answer from one side."
            )
        if operation == "join":
            left, right = _coerce_keys(frames[0], frames[1], join_keys)
            left_on = [_column(left, k.get("left")) for k in join_keys]
            right_on = [_column(right, k.get("right")) for k in join_keys]
            return left.join(right, left_on=left_on, right_on=right_on, how="inner", suffix="_right")
        if operation == "compare":
            left, right = _coerce_keys(frames[0], frames[1], join_keys)
            left_on = [_column(left, k.get("left")) for k in join_keys]
            right_on = [_column(right, k.get("right")) for k in join_keys]
            joined = left.join(right, left_on=left_on, right_on=right_on, how="inner", suffix="_right")
            diff_cols = []
            joined_columns = _names(joined)
            for col in _names(left):
                if col in left_on:
                    continue
                right_col = f"{col}_right"
                if right_col in joined_columns:
                    diff_cols.append((col, right_col))
            if not diff_cols:
                return joined
            diff_exprs = [(pl.col(l) != pl.col(r)) for l, r in diff_cols]
            return joined.filter(pl.any_horizontal(diff_exprs))
        raise ValueError(f"Unsupported combine operation '{operation}'.")

    def unconverted_join_keys(
        self,
        inputs: List[Tuple[str, Frame]],
        join_keys: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Each side's join-key values that did not convert to the type the join casts to.

        ``inputs`` are ``(sub-query id, frame)`` in engine order. One entry per
        side and key with any: ``sub_query_id``, ``column``, ``failed`` (values
        that became null), ``total`` (values that were not null) and ``dtype``
        (the type they were cast to). Counting reads the key columns, so it
        collects them; a join of typed keys only.
        """
        if len(inputs) < 2:
            return []
        found = []
        (left_id, left), (right_id, right) = inputs[0], inputs[1]
        for key in join_keys:
            types = _key_types(left, right, key)
            if types is None:
                continue
            left_column, right_column, target = types
            for source_id, frame, column, declared in ((left_id, left, left_column, key["left_dtype"]),
                                                       (right_id, right, right_column, key["right_dtype"])):
                original = pl.col(column)
                failed, total = frame.select(
                    (original.is_not_null() & _coerced_key(frame, column, declared, target).is_null())
                    .sum().alias("failed"),
                    original.is_not_null().sum().alias("total"),
                ).collect().row(0)
                if failed:
                    found.append({"sub_query_id": source_id, "column": column, "failed": failed, "total": total,
                                  "dtype": target})
        return found

    def post_op(self, operation: str, frame: Frame, attributes: Dict[str, Any]) -> Frame:
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
        _check_attributes(frame, post_op_columns(operation, attributes))
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

    def to_rows(self, frame: Frame) -> List[Dict[str, Any]]:
        if isinstance(frame, pl.LazyFrame):
            frame = frame.collect()
        return frame.to_dicts()
