"""``engine.results``: the results a run saved, by its ``trace_id``: the manifest, a page, CSV or a chart.

Readable only by the caller who asked the run: every method takes the caller as ``user_context``, and another
caller's run is the same ``ResultNotFound`` as a missing one."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Iterator, Optional, Union

from nl2sql.charts.spec import ChartSpec
from nl2sql.execution.artifacts.results import (
    DEFAULT_RESULT_PAGE_SIZE,
    ResultNotFound,
    ResultManifest,
    ResultOwner,
    ResultPage,
    ResultReader,
)
from nl2sql.runtime import Runtime

if TYPE_CHECKING:
    from nl2sql.api.query_api import QueryResult
    from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
    from nl2sql.policies import UserContext


def _single_plan(result: "QueryResult") -> Optional["PlanModel"]:
    """The plan of a run with exactly one sub-query: it refines the result's column kinds. With several
    sub-queries (an aggregated result), or none (a verified query), there is none."""
    if len(result.sub_queries) != 1 or not result.sub_queries[0].plan:
        return None
    from pydantic import ValidationError

    from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel

    try:
        return PlanModel.model_validate(result.sub_queries[0].plan)
    except ValidationError:
        return None


def _single_intent(result: "QueryResult") -> str:
    return (result.sub_queries[0].intent if len(result.sub_queries) == 1 else "") or "Result"


class ResultsAPI:
    """``engine.results``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def _reader(self, user_context: "UserContext") -> ResultReader:
        """A reader of the runs ``user_context`` asked, in the engine's tenant folder (where every run saves).

        Raises:
            TypeError: ``user_context`` is not a ``UserContext``. There is no default caller: failing open would
                let anyone read a run whose trace id they saw.
        """
        from nl2sql.policies import UserContext

        if not isinstance(user_context, UserContext):
            raise TypeError("user_context is required: pass the caller's UserContext. A run's results are "
                            "readable only by the caller who asked it.")
        tenant = getattr(self._ctx, "tenant_id", None)
        # The owner is worked out exactly as the writer works it out (``write_manifest``).
        return ResultReader(self._ctx.artifact_store, tenant or "", ResultOwner.of(user_context, tenant))

    def manifest(self, run_id: str, *, user_context: "UserContext") -> ResultManifest:
        """Which results the run ``run_id`` (its ``trace_id``) saved: per result,
        its node id, columns, row count and truncated flag, and who asked. Never the rows.

        Raises:
            ResultNotFound: no such run, a run another caller asked (another user, or the same user id in
                another tenant), or ``run_id`` is not a plain token.
            TypeError: ``user_context`` is not a ``UserContext``.
        """
        return self._reader(user_context).manifest(run_id)

    def page(self, run_id: str, result_id: str, offset: int = 0, limit: int = DEFAULT_RESULT_PAGE_SIZE, *,
             user_context: "UserContext") -> ResultPage:
        """Rows ``offset`` to ``offset + limit`` of one saved result, read lazily
        from its parquet file. ``result_id`` is a node id from
        ``QueryResult.result_manifest``. ``limit`` is capped at
        ``MAX_RESULT_PAGE_SIZE`` (1,000); a page past the end is empty.
        ``next_offset`` is where the next page starts, or None at the end.

        Raises:
            ResultNotFound: no such run or result for this caller.
            TypeError: ``user_context`` is not a ``UserContext``.
            ValueError: a negative ``offset`` or a ``limit`` below 1.
        """
        return self._reader(user_context).page(run_id, result_id, offset=offset, limit=limit)

    def csv(self, run_id: str, result_id: str, *, user_context: "UserContext") -> Iterator[bytes]:
        """The whole of one saved result as UTF-8 CSV, header first, yielded a
        slice at a time so it is never built in memory. The ids and the caller are
        checked before the iterator is returned.

        Raises:
            ResultNotFound: no such run or result for this caller.
            TypeError: ``user_context`` is not a ``UserContext``.
        """
        return self._reader(user_context).csv(run_id, result_id)

    def chart(self, result: Union["QueryResult", str], result_id: Optional[str] = None, *,
              user_context: "UserContext", kind: Optional[str] = None) -> ChartSpec:
        """The chart the data of ``result`` (a ``QueryResult``, or a ``trace_id``) is drawn as.

        Five rules choose a line, a bar, a big number or a table from the saved result's column kinds, with no
        model call; a ``QueryResult`` that had one sub-query also lets its plan refine the kinds. The rows are
        read as ``page`` reads them, by ``trace_id`` and caller, up to ``settings.charts.max_points``.
        ``result_id`` names the saved result (a node id) and may be left out when the run saved one.
        ``kind`` forces a kind the data can draw.

        Raises:
            ResultNotFound: no such run or result for this caller.
            TypeError: ``user_context`` is not a ``UserContext``.
            ValueError: ``kind`` is one the data cannot draw (the message names those it can), or the run saved
                several results and ``result_id`` names none.
        """
        from nl2sql.charts.choose import choose

        reader = self._reader(user_context)
        if isinstance(result, str):
            trace_id, plan, title = result, None, "Result"
            saved = [r.node_id for r in reader.manifest(trace_id).results]
        else:
            # A run a hook withheld has an empty `result_manifest` though its files are on disk: only the
            # results the caller was given may be charted.
            trace_id, plan, title = result.trace_id, _single_plan(result), _single_intent(result)
            saved = [r.node_id for r in result.result_manifest]
            if not saved or (result_id is not None and result_id not in saved):
                raise ResultNotFound(f"No result '{result_id or ''}' for request '{trace_id}'.")
        if result_id is None and len(saved) > 1:
            raise ValueError(f"Run '{trace_id}' saved several results; name one of: {', '.join(saved)}.")
        wanted = result_id if result_id is not None else (saved[0] if saved else "")
        settings = self._ctx.settings.charts
        summary, rows = reader.head(trace_id, wanted, settings.max_points)
        return choose(columns=summary.columns, kinds=summary.column_kinds,
                      rows=[[float(v) if isinstance(v, Decimal) else v for v in row] for row in rows],
                      row_count=summary.row_count, title=title, trace_id=trace_id, result_id=summary.node_id,
                      settings=settings, plan=plan, kind=kind, cut=summary.truncated)
