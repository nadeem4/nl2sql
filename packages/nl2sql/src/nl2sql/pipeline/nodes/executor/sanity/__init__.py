"""The answer sanity checks: cheap checks after a sub-query runs, before the aggregator.

``values`` (an empty result filtered on a value its column lacks:
``VALUE_NOT_FOUND``), ``fanout`` (an aggregate over rows a join repeated:
``JOIN_FAN_OUT``) and ``nulls`` (a result column with no value:
``ALL_NULL_COLUMN``). Every probe is a sqlglot tree built from the plan and
rendered by the adapter, bounded, and never run on a table the caller's scope
denies. A check that fails to run reports nothing. The executor runs them
after ``execute.after``; ``settings.pipeline.sanity_checks: false`` turns them
off. A probe is SQL the hooks never see (it runs outside ``execute.before``
and ``execute.after``), so with ``probes`` false (a hook is attached at
``execute.before``) the probing checks are skipped and only the null check,
which reads the saved result, runs. See docs/architecture/nodes/executor_node.md.
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional

import polars as pl

from nl2sql.common.errors import ErrorCode, PipelineError
from nl2sql.common.logger import get_logger
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel, TableRef

from .fanout import check_fan_out
from .nulls import check_nulls
from .values import check_values

logger = get_logger("sanity")

__all__ = ["run_checks", "check_values", "check_fan_out", "check_nulls"]


def _samples(tables: List[Any]) -> Callable[[str, str], Optional[List[Any]]]:
    """The snapshot's sample values of a column, from the sub-query's tables."""
    by_name = {str(t.name).lower(): t for t in tables}

    def samples(table: str, column: str) -> Optional[List[Any]]:
        found = by_name.get(table.lower())
        for col in (found.columns if found is not None else []):
            if str(col.name).lower() == column.lower():
                return list((col.stats or {}).get("sample_values") or [])
        return None

    return samples


def _nulls(opened: Optional[pl.LazyFrame]) -> List[PipelineError]:
    return check_nulls(opened) if opened is not None else []


def run_checks(
    plan: PlanModel,
    *,
    adapter: Any,
    allowed: Callable[[TableRef], bool],
    tables: List[Any],
    row_limit: Optional[int],
    row_count: Optional[int],
    frame: Callable[[], Optional[pl.LazyFrame]],
    can_retry: Callable[[ErrorCode], bool],
    probes: bool = True,
) -> List[PipelineError]:
    """Every finding for one sub-query's result.

    ``row_count`` 0 runs the value check; a result with rows runs the fan-out
    and null checks (``frame`` opens the saved result). ``can_retry(code)``
    says whether a finding of ``code`` may still be retried. ``probes`` false
    skips the checks that query the database (values, fan-out).
    """
    checks: List[Callable[[], List[PipelineError]]] = []
    if row_count == 0 and probes:
        checks.append(lambda: check_values(plan, adapter, allowed, can_retry(ErrorCode.VALUE_NOT_FOUND),
                                           row_limit, samples=_samples(tables)))
    elif row_count:
        if probes:
            checks.append(lambda: check_fan_out(plan, adapter, allowed, tables, can_retry(ErrorCode.JOIN_FAN_OUT)))
        checks.append(lambda: _nulls(frame()))
    found: List[PipelineError] = []
    for check in checks:
        try:
            found.extend(check())
        except Exception as exc:  # a check that fails to run reports nothing
            logger.warning(f"Sanity check skipped: {exc}")
    return found
