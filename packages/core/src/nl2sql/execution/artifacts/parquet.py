from __future__ import annotations

from typing import Any, Optional, Dict

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from nl2sql_adapter_sdk.contracts import ResultFrame


def result_frame_to_polars(frame: ResultFrame) -> pl.DataFrame:
    rows = frame.to_row_dicts()
    columns = [c.name for c in frame.columns]
    if columns:
        return pl.DataFrame(rows, schema=columns)
    return pl.DataFrame(rows)


def write_parquet_polars(
    df: pl.DataFrame,
    target: Any,
    storage_options: Optional[Dict[str, Any]] = None,
) -> None:
    if storage_options is None:
        df.write_parquet(target)
    else:
        df.write_parquet(target, storage_options=storage_options)


def polars_df_to_result_frame(df: pl.DataFrame) -> ResultFrame:
    rows = df.to_dicts()
    columns = df.columns
    return ResultFrame.from_row_dicts(rows, columns=columns, row_count=len(rows), success=True)


def read_parquet_polars(
    source: Any,
    storage_options: Optional[Dict[str, Any]] = None,
) -> ResultFrame:
    if storage_options is None:
        df = pl.read_parquet(source)
    else:
        df = pl.read_parquet(source, storage_options=storage_options)
    return polars_df_to_result_frame(df)


def table_to_result_frame(table: pa.Table) -> ResultFrame:
    rows = table.to_pylist()
    columns = list(table.column_names)
    return ResultFrame.from_row_dicts(rows, columns=columns, row_count=len(rows), success=True)


def read_parquet(source: Any) -> pa.Table:
    return pq.read_table(source)
