"""``engine.results``: the results a run saved, by its ``trace_id``: the manifest, a page, or CSV."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator, Optional, Union

from nl2sql.charts.spec import ChartSpec
from nl2sql.common.planned import arrives_with
from nl2sql.execution.artifacts.results import (
    DEFAULT_RESULT_PAGE_SIZE,
    ResultManifest,
    ResultPage,
    ResultReader,
)
from nl2sql.runtime import Runtime

if TYPE_CHECKING:
    from nl2sql.api.query_api import QueryResult


class ResultsAPI:
    """``engine.results``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def _reader(self, tenant_id: Optional[str]) -> ResultReader:
        """A reader of one tenant's saved results: ``tenant_id``, else the engine's own."""
        return ResultReader(self._ctx.artifact_store, tenant_id or getattr(self._ctx, "tenant_id", None) or "")

    def manifest(self, run_id: str, tenant_id: Optional[str] = None) -> ResultManifest:
        """Which results the run ``run_id`` (its ``trace_id``) saved: per result,
        its node id, columns, row count and truncated flag. Never the rows.

        Raises:
            ResultNotFound: no such run for the tenant (the engine's own unless
                ``tenant_id`` is given), or ``run_id`` is not a plain token.
        """
        return self._reader(tenant_id).manifest(run_id)

    def page(self, run_id: str, result_id: str, offset: int = 0, limit: int = DEFAULT_RESULT_PAGE_SIZE,
             tenant_id: Optional[str] = None) -> ResultPage:
        """Rows ``offset`` to ``offset + limit`` of one saved result, read lazily
        from its parquet file. ``result_id`` is a node id from
        ``QueryResult.result_manifest``. ``limit`` is capped at
        ``MAX_RESULT_PAGE_SIZE`` (1,000); a page past the end is empty.
        ``next_offset`` is where the next page starts, or None at the end.

        Raises:
            ResultNotFound: no such run or result for the tenant.
            ValueError: a negative ``offset`` or a ``limit`` below 1.
        """
        return self._reader(tenant_id).page(run_id, result_id, offset=offset, limit=limit)

    def csv(self, run_id: str, result_id: str, tenant_id: Optional[str] = None) -> Iterator[bytes]:
        """The whole of one saved result as UTF-8 CSV, header first, yielded a
        slice at a time so it is never built in memory. The ids are checked
        before the iterator is returned.

        Raises:
            ResultNotFound: no such run or result for the tenant.
        """
        return self._reader(tenant_id).csv(run_id, result_id)

    def chart(self, result: Union["QueryResult", str], result_id: Optional[str] = None, *, kind: Optional[str] = None,
              tenant_id: Optional[str] = None) -> ChartSpec:
        """The chart the data of ``result`` (a ``QueryResult``, or a ``trace_id``) is drawn as; ``kind`` forces
        one the data can draw. K1 fills this (docs/architecture/wave_c.md, section 5)."""
        raise arrives_with("engine.results.chart", "K1")
