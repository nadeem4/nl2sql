"""A conversation's Markdown report (docs/architecture/wave_c.md, section 4, "Reports").

Assembled from the stored turns with no model call (D114): each answered question with the plain-English
explanation the run stored, the first rows read back through ``engine.results``, the takeaway of the chart the
data is drawn as, the SQL and the trace id. A result that has been purged is said to have expired; the text,
the SQL and the trace stay. The rows are read when the report is asked for and never stored (D106): the
``report`` tool's stored output is the report without them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from importlib import metadata
from typing import Any, List, Optional, Sequence

from nl2sql.conversations.models import Conversation, ToolStep, Turn
from nl2sql.execution.artifacts.results import ResultNotFound

ROW_LIMIT = 20


def _version() -> str:
    try:
        return metadata.version("nl2sql-engine")
    except metadata.PackageNotFoundError:
        return "unknown"


def _cell(value: Any) -> str:
    return "" if value is None else str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _table(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> List[str]:
    lines = ["| " + " | ".join(_cell(c) for c in columns) + " |", "|" + " --- |" * len(columns)]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return lines


def _rows(results: Any, step: ToolStep) -> List[str]:
    """The first rows of the step's result as a table, or the reason there are none."""
    manifest = step.result.result_manifest if step.result else []
    if not manifest:
        return ["_No rows were released for this answer._"]
    try:
        page = results.page(step.trace_id, manifest[0].node_id, limit=ROW_LIMIT)
    except ResultNotFound:
        return ["_Results expired._"]
    lines = _table(page.columns, page.rows)
    if page.total_rows > len(page.rows):
        lines += ["", f"_Showing the first {len(page.rows)} of {page.total_rows} rows._"]
    return lines


def _takeaway(results: Any, step: ToolStep) -> Optional[str]:
    """The sentence of the chart the data is drawn as, unless it is a table or the rows are gone."""
    try:
        chart = results.chart(step.result, step.result.result_manifest[0].node_id)
    except (ResultNotFound, ValueError):
        return None
    return chart.takeaway if chart.kind != "table" and chart.takeaway else None


def _sql(step: ToolStep) -> Optional[str]:
    queries = [sq for sq in step.result.sub_queries if sq.sql]
    if not queries:
        return None
    text = "\n\n".join((f"-- {sq.id}\n" if len(queries) > 1 else "") + sq.sql for sq in queries)
    return f"<details><summary>SQL</summary>\n\n```sql\n{text}\n```\n\n</details>"


def _section(results: Any, step: ToolStep, include_rows: bool) -> str:
    result = step.result
    parts = [f"## {step.question}"]
    summary = (result.final_answer or {}).get("summary")
    if summary:
        parts.append(str(summary))
    if result.explanation and result.explanation.steps:
        parts.append("**Why this answer.** " + " ".join(s.text for s in result.explanation.steps))
    if include_rows:
        parts.append("\n".join(_rows(results, step)))
        if result.result_manifest and (takeaway := _takeaway(results, step)):
            parts.append(f"**Takeaway.** {takeaway}")
    if (sql := _sql(step)):
        parts.append(sql)
    parts.append(f"Trace: {step.trace_id}")
    return "\n\n".join(parts)


def _chosen(conversation: Conversation, turns: Optional[Sequence[int]]) -> List[Turn]:
    if turns is None:
        return list(conversation.turns)  # the step filter in build_report keeps only answered questions
    by_index = {t.index: t for t in conversation.turns}
    missing = [i for i in turns if i not in by_index]
    if missing:
        raise ValueError(f"There is no turn {', '.join(map(str, missing))} in this conversation.")
    return [by_index[i] for i in dict.fromkeys(turns)]


def build_report(conversation: Conversation, results: Any, *, turns: Optional[Sequence[int]] = None,
                 title: Optional[str] = None, include_rows: bool = True) -> str:
    """The report of the answered questions of every turn (or of ``turns``), as Markdown. With ``include_rows``
    false the report holds no row data (no table, no chart takeaway): that is the form a stored tool output has.

    Raises:
        ValueError: a turn that does not exist, or no answered question to report.
    """
    steps = [s for t in _chosen(conversation, turns) for s in t.steps
             if s.call.name == "ask" and s.status == "ok" and s.result is not None]
    if not steps:
        raise ValueError("This conversation has no answered question to report.")
    today = datetime.now(timezone.utc).date().isoformat()
    head = f"# {title or conversation.title}\nGenerated {today} · {len(steps)} questions · nl2sql {_version()}"
    return "\n\n".join([head, *(_section(results, s, include_rows) for s in steps)]) + "\n"
