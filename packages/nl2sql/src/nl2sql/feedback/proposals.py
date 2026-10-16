"""A thumbs-up generated answer as a proposed verified query (docs/architecture/wave_b.md, section 5, D43).

A thumbs-up on a *generated* answer is a candidate verified query: the
question the person asked and the SQL that answered it well. ``verified_query``
builds the proposal's ``change`` in ``curated.yaml``'s shape
(:class:`nl2sql.config.curated.VerifiedQuery`): an id derived from the
question, the question as the one phrasing, the run's SQL and a description
placeholder. ``engine.feedback.record`` files it in the review queue; it reaches
``curated.yaml`` only when an admin accepts it.

The values in the SQL are the literal ones the run used. Turning them into
``{params}``, dropping the generator's row-cap ``LIMIT`` and writing the
description are the reviewer's edits on accept.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Mapping

DESCRIPTION_PLACEHOLDER = ("TODO: say what this query returns. The SQL holds the run's literal values: turn the "
                           "ones a person would change into {params}, and drop the row-cap LIMIT the generator "
                           "added unless the question asks for it.")

SKIP_REASONS = ("not_thumbs_up", "verified", "failed", "no_question", "no_sql", "multiple_statements",
                "no_datasource", "multiple_datasources")

_ID_MAX = 60


def _slug(question: str) -> str:
    """A ``curated.yaml`` id for ``question``: lower case, ``_`` between words, starting with a letter."""
    slug = re.sub(r"[^a-z0-9]+", "_", question.lower()).strip("_")[:_ID_MAX].rstrip("_")
    return slug if slug[:1].isalpha() else f"q_{slug}"


def skip_reason(row: Mapping[str, Any]) -> str:
    """Why the feedback ``row`` cannot be proposed as a verified query, or "" when it can."""
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
        return "no_datasource"
    if len(datasources) > 1:
        return "multiple_datasources"
    return ""


def verified_query(row: Mapping[str, Any]) -> Dict[str, Any]:
    """The proposed verified query of a row ``skip_reason`` passes: ``{id, description, questions, sql}``."""
    question = " ".join(row["question"].split())
    sql = next(s for s in row["sql"] if s)
    return {"id": _slug(question), "description": DESCRIPTION_PLACEHOLDER, "questions": [question],
            "sql": sql.strip() + "\n"}
