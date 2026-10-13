"""Proposed verified queries from thumbs-up runs, for a person to review.

A thumbs-up on a *generated* answer is a candidate verified query: the
question the person asked and the SQL that answered it well. The proposals
have ``curated.yaml``'s shape (:class:`nl2sql.configs.curated.CuratedFileConfig`):
an id derived from the question, the question as the one phrasing, the run's
SQL and a description placeholder, under the datasource the run used.

The values in the SQL are the literal ones the run used. Turning them into
``{params}`` is the reviewer's job, as is the description; reviewing the file
and committing the entries into ``curated.yaml`` is the approval. Nothing here
writes ``curated.yaml``, and :func:`write_proposals` refuses to.
"""
from __future__ import annotations

import pathlib
import re
from typing import Any, Dict, Iterable, List, Mapping, Tuple

import yaml

HEADER = (
    "# Proposed verified queries, exported from thumbs-up runs by `nl2sql curated proposals export`.\n"
    "# Nothing reads this file. A person reviews each entry before it joins curated.yaml:\n"
    "#   - check the SQL answers the question, and write the description;\n"
    "#   - every value in the SQL is the literal one the run used: turn the ones a person\n"
    "#     would change into {params} in the question, :params in the SQL and a params: entry;\n"
    "#   - drop the row-cap LIMIT the generator added, unless the question asks for it;\n"
    "#   - add other phrasings, then move the entry into curated.yaml and run\n"
    "#     `nl2sql curated validate`. Committing it is the approval.\n"
)

DESCRIPTION_PLACEHOLDER = "TODO: say what this query returns."

SKIP_REASONS = ("not_thumbs_up", "verified", "failed", "no_question", "no_sql", "multiple_statements",
                "no_datasource", "multiple_datasources", "duplicate")

_ID_MAX = 60


def _slug(question: str) -> str:
    """A ``curated.yaml`` id for ``question``: lower case, ``_`` between words, starting with a letter."""
    slug = re.sub(r"[^a-z0-9]+", "_", question.lower()).strip("_")[:_ID_MAX].rstrip("_")
    return slug if slug[:1].isalpha() else f"q_{slug}"


def _skip_reason(row: Mapping[str, Any]) -> str:
    """Why ``row`` cannot be proposed, or "" when it can."""
    if row.get("rating") != "up":
        return "not_thumbs_up"
    if row.get("source") == "verified":
        return "verified"
    if row.get("status") != "success":
        return "failed"
    if not (row.get("question") or "").strip():
        return "no_question"
    sql = [s for s in row.get("sql") or [] if s]
    if not sql:
        return "no_sql"
    if len(sql) > 1:
        return "multiple_statements"
    datasources = row.get("datasource_ids")
    if not datasources:
        return "no_datasource"  # rated before the feedback table recorded it
    if len(datasources) > 1:
        return "multiple_datasources"
    return ""


def build_proposals(rows: Iterable[Mapping[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """The proposals as a ``curated.yaml`` document, and how many rows were skipped, by reason.

    ``rows`` are feedback rows, newest first (as :meth:`FeedbackStore.list`
    returns them), so of several runs of the same question (ignoring case and
    spacing) on one datasource, the newest is proposed.
    """
    skipped = {reason: 0 for reason in SKIP_REASONS}
    datasources: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    seen = set()
    for row in rows:
        reason = _skip_reason(row)
        if reason:
            skipped[reason] += 1
            continue
        question = " ".join(row["question"].split())
        datasource = row["datasource_ids"][0]
        key = (datasource, question.lower())
        if key in seen:
            skipped["duplicate"] += 1
            continue
        seen.add(key)
        queries = datasources.setdefault(datasource, {"verified_queries": []})["verified_queries"]
        ids = {q["id"] for q in queries}
        qid, n = _slug(question), 2
        while qid in ids:
            qid, n = f"{_slug(question)}_{n}", n + 1
        sql = next(s for s in row["sql"] if s)
        queries.append({
            "id": qid,
            "description": DESCRIPTION_PLACEHOLDER,
            "questions": [question],
            "sql": sql.strip() + "\n",
        })
    return {"version": 1, "datasources": datasources}, skipped


class _Dumper(yaml.SafeDumper):
    """Writes multi-line strings (the SQL) as ``|`` blocks, as a person would."""


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)


def render_proposals(document: Mapping[str, Any]) -> str:
    """``document`` as YAML, under the header telling the reviewer what to change."""
    body = yaml.dump(dict(document), Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120)
    return HEADER + "\n" + body


def write_proposals(text: str, path: pathlib.Path, *, protected: Iterable[pathlib.Path]) -> None:
    """Writes ``text`` to ``path``, unless ``path`` is one of ``protected`` (the curated file).

    Raises ``ValueError`` without writing when it is: proposals never go into
    ``curated.yaml`` itself.
    """
    path = pathlib.Path(path)
    target = path.resolve()
    if any(target == pathlib.Path(p).resolve() for p in protected):
        raise ValueError(f"{path} is the curated answers file (curated.yaml); proposals never go into it. "
                         "Write them to another file and review them there.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
