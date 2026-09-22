"""Draft gold entries from thumbs-up runs, for a human to review.

Each draft has the shape of an entry in ``chinook_gold.yaml``
(:class:`nl2sql.evaluation.gold.GoldQuestion`): the question, the run's SQL as
the ``gold_sql`` candidate, empty tags and placeholders for everything a
reviewer must decide. ``gold_result`` is left empty: the gold generator writes
it. Drafts go to their own file; the gold set itself is never written here.
"""
from __future__ import annotations

import pathlib
from typing import Any, Dict, Iterable, List, Mapping, Tuple

import yaml

from nl2sql.evaluation.gold import GoldQuestion

HEADER = (
    "# Draft gold entries exported from thumbs-up runs by `nl2sql feedback export --good`.\n"
    "# A person must review every entry before it joins the gold set: check the SQL answers\n"
    "# the question, fill in difficulty, tags, needed_tables, needed_columns and expected,\n"
    "# then copy it into chinook_gold.yaml and run `python -m nl2sql.evaluation.gold`\n"
    "# to generate its gold_result. Nothing reads this file.\n"
)


def draft_entries(rows: Iterable[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """The draft entries for the thumbs-up rows, and how many were skipped.

    A run is skipped when it has no SQL, or more than one statement:
    ``gold_sql`` is a single query.
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
        entry = GoldQuestion(
            id=f"draft-{str(row['trace_id'])[:8]}",
            question=row.get("question") or "",
            difficulty="medium",
            tags=[],
            needed_tables=[],
            needed_columns=[],
            expected={row.get("role") or "admin": "allowed"},
            order_matters=False,
            gold_sql=sql[0].strip() + "\n",
            gold_result=None,
        ).model_dump(exclude={"paraphrase_group"})
        drafts.append(entry)
    return drafts, skipped


def write_drafts(drafts: List[Dict[str, Any]], path: pathlib.Path) -> None:
    from nl2sql.evaluation.gold import _Dumper  # the gold file's own YAML style

    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.dump(drafts, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120,
                     default_flow_style=None) if drafts else "[]\n"
    path.write_text(HEADER + "\n" + body, encoding="utf-8", newline="\n")
