"""All-null result columns (``ALL_NULL_COLUMN``).

A result with rows whose column holds only nulls is usually an outer join that
matched nothing, or the wrong column. There is no reliable fix to suggest and
nothing to probe, so it is a warning only. It reads the saved result's null
counts, never its values.
"""
from __future__ import annotations

from typing import List

import polars as pl

from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError


def check_nulls(frame: pl.LazyFrame) -> List[PipelineError]:
    """An ``ALL_NULL_COLUMN`` warning for each column of ``frame`` with rows and no value."""
    counts = frame.select(pl.len().alias("__rows__"), pl.all().null_count()).collect()
    if counts.is_empty():
        return []
    row = counts.row(0, named=True)
    rows = row.pop("__rows__")
    if not rows:
        return []
    return [
        PipelineError(
            node="executor",
            message=(f"Every value of '{column}' in the result is empty: an outer join may have matched "
                     "nothing, or the column may not be the one the question means."),
            severity=ErrorSeverity.WARNING,
            error_code=ErrorCode.ALL_NULL_COLUMN,
        )
        for column, nulls in row.items() if nulls == rows
    ]
