"""``regenerate``: write a suite's ``rows`` and ``alt_rows`` by running its SQL through the adapter.

For each approved case with SQL, the case's datasource adapter (from the
engine's ``DatasourceRegistry``) runs ``sql`` and every ``alt_sql``,
read-only and within the datasource's limits, and the rows are written back
into the suite in the order the SQL returns them. The expected rows come from
the same adapter questions run on, never from a driver of their own.

A case whose SQL fails, or whose result was cut at the datasource's row
limit, is reported and left unchanged; the others are still written. The
file keeps its leading comment lines and its layout (see
:func:`nl2sql.evaluation.suite.dump_suite`). Rows are never edited by hand: a
new accepted answer is a reviewed ``alt_sql``, then ``regenerate``.
"""
from __future__ import annotations

import pathlib
from typing import Any, Dict, List

from nl2sql_sdk.contracts import AdapterRequest
from pydantic import BaseModel, Field

from nl2sql.evaluation.suite import SUITES_DIR, EvalCase, file_header, load_suite, write_suite


class RegenerateReport(BaseModel):
    """What ``regenerate`` wrote: one count per result set, and each case left unchanged with why."""

    path: pathlib.Path
    written: int = 0
    failed: List[Dict[str, str]] = Field(default_factory=list)  # {"case", "error"}


class _Failed(Exception):
    pass


def _rows(ds_registry: Any, datasource_id: str, sql: str) -> List[Dict[str, Any]]:
    frame = ds_registry.get_adapter(datasource_id).execute(AdapterRequest(plan_type="sql", payload={"sql": sql}))
    if not frame.success:
        raise _Failed(frame.error.safe_message if frame.error else "the query failed")
    if frame.truncated:
        raise _Failed(f"the result was cut at the datasource's row limit ({frame.row_count} rows)")
    return [dict(zip(frame.columns, row)) for row in frame.rows]


def _regenerated(ds_registry: Any, case: EvalCase) -> EvalCase:
    if len(case.datasources) != 1:
        raise _Failed(f"a case with sql names exactly one datasource, not {case.datasources}")
    datasource_id = case.datasources[0]
    sql = case.sql.strip() + "\n"
    alternatives = [alt.strip() + "\n" for alt in case.alt_sql]
    return case.model_copy(update={
        "sql": sql, "rows": _rows(ds_registry, datasource_id, sql), "alt_sql": alternatives,
        "alt_rows": [_rows(ds_registry, datasource_id, alt) for alt in alternatives]})


def regenerate(ds_registry: Any, path: pathlib.Path, *, allow_packaged: bool = False) -> RegenerateReport:
    """Re-run every approved case's ``sql`` and ``alt_sql`` through its adapter and rewrite the suite in place.

    A suite the package ships (``nl2sql/evaluation/suites/``) is refused
    unless ``allow_packaged``: in an installed package it is not yours to
    rewrite. The repo's own workflow passes it, from a checkout, to
    regenerate ``suites/chinook.yaml`` after a reviewed ``alt_sql`` is added.

    Raises:
        ValueError: ``path`` is a packaged suite and ``allow_packaged`` is off.
    """
    path = pathlib.Path(path)
    if not allow_packaged and path.resolve().parent == SUITES_DIR.resolve():
        raise ValueError(f"{path} is a suite the package ships. Copy it into your project, or pass "
                         "allow_packaged=True from a checkout of the repo.")
    header = file_header(path)
    suite = load_suite(path)
    report = RegenerateReport(path=path)
    cases = []
    for case in suite.cases:
        if case.status != "approved" or case.sql is None:
            cases.append(case)
            continue
        try:
            new = _regenerated(ds_registry, case)
        except Exception as exc:  # one broken case leaves the rest to be written
            report.failed.append({"case": case.id, "error": str(exc)})
            cases.append(case)
            continue
        report.written += 1 + len(new.alt_sql)
        cases.append(new)
    write_suite(path, {"version": suite.version, "cases": [c.model_dump(mode="json") for c in cases]}, header)
    return report
