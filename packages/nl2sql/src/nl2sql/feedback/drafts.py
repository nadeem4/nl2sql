"""Draft eval cases from thumbs-up runs, for a person to review.

Each draft is a case in the suite format (:class:`nl2sql.evaluation.suite.EvalCase`)
with ``status: draft`` and ``origin: feedback``: the question, the run's SQL
as the ``sql`` candidate, the datasources it read, the rating role allowed,
and nothing a reviewer must decide. ``rows`` is left empty: ``regenerate``
writes it once the case is approved. Drafts never run in an eval, and go to
their own file; a suite the package ships is never written here.
"""
from __future__ import annotations

import pathlib
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from nl2sql.evaluation.suite import EvalCase, write_suite

HEADER = (
    "# Draft eval cases exported from thumbs-up runs by `nl2sql feedback export --good`.\n"
    "# A person reviews every case before it runs: check the SQL answers the question, fill in\n"
    "# difficulty, tags, tables, columns and the outcome for each role, then set status: approved\n"
    "# and run engine.evals.regenerate on the file to write its rows. Drafts never run in an eval.\n"
    "\n"
)


def draft_entries(rows: Iterable[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """The draft cases for the thumbs-up rows, and how many were skipped.

    A run is skipped when it has no SQL, or more than one statement: a
    case's ``sql`` is a single query.
    """
    drafts: List[Dict[str, Any]] = []
    skipped = 0
    for row in rows:
        if row.get("rating") != "up":
            continue
        sql = [s for s in row.get("sql") or [] if s]
        if len(sql) != 1:
            skipped += 1
            continue
        drafts.append(EvalCase(
            id=f"draft-{str(row['trace_id'])[:8]}",
            question=row.get("question") or "",
            datasources=list(row.get("datasource_ids") or []),
            status="draft",
            origin="feedback",
            outcome={row.get("role") or "admin": "allowed"},
            sql=sql[0].strip() + "\n",
        ).model_dump(mode="json"))
    return drafts, skipped


def write_drafts(drafts: List[Dict[str, Any]], path: pathlib.Path) -> None:
    """Write the drafts as a suite file, with a header saying how to review them."""
    write_suite(pathlib.Path(path), {"version": 1, "cases": drafts}, HEADER)
