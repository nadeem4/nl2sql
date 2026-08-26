from __future__ import annotations

from typing import Any, Dict, Optional

import polars as pl

from nl2sql_adapter_sdk.contracts import ResultFrame


def result_frame_to_polars(frame: ResultFrame) -> pl.DataFrame:
    rows = frame.to_row_dicts()
    columns = frame.columns
    if columns:
        return pl.DataFrame(rows, schema=columns)
    return pl.DataFrame(rows)


def polars_to_result_frame(df: pl.DataFrame) -> ResultFrame:
    rows = df.to_dicts()
    columns = df.columns
    return ResultFrame.from_row_dicts(rows, columns=columns, row_count=len(rows), success=True)


def write_parquet(
    df: pl.DataFrame,
    target: Any,
    storage_options: Optional[Dict[str, Any]] = None,
) -> None:
    if storage_options is None:
        df.write_parquet(target)
    else:
        df.write_parquet(target, storage_options=storage_options)


def read_parquet(
    source: Any,
    storage_options: Optional[Dict[str, Any]] = None,
) -> pl.DataFrame:
    if storage_options is None:
        return pl.read_parquet(source)
    return pl.read_parquet(source, storage_options=storage_options)
