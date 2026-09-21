"""The Chinook gold evaluation dataset: its model, loader and result generator.

``datasets/chinook_gold.yaml`` is hand-written except for ``gold_result``,
which is always produced by running ``gold_sql`` against the vendored Chinook
database. After editing a question or its SQL, regenerate the results with::

    python -m nl2sql.evaluation.gold
"""
from __future__ import annotations

import pathlib
import sqlite3
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict

GOLD_DATASET_PATH = pathlib.Path(__file__).parent / "datasets" / "chinook_gold.yaml"
CHINOOK_DB_PATH = pathlib.Path(__file__).parents[1] / "cli" / "demo" / "data" / "chinook.sqlite"

Outcome = Literal["allowed", "refused", "unanswerable"]
Difficulty = Literal["easy", "medium", "hard"]


class GoldQuestion(BaseModel):
    """One question with its hand-written SQL and the rows that SQL returns."""

    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    difficulty: Difficulty
    tags: list[str]
    paraphrase_group: Optional[str] = None
    needed_tables: list[str]
    needed_columns: list[str]
    expected: dict[str, Outcome]
    order_matters: bool
    gold_sql: Optional[str]
    gold_result: Optional[list[dict[str, Any]]]


def load_gold_dataset(path: pathlib.Path = GOLD_DATASET_PATH) -> list[GoldQuestion]:
    """Load and validate the dataset."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [GoldQuestion.model_validate(item) for item in raw]


def execute_gold_sql(sql: str, db_path: pathlib.Path = CHINOOK_DB_PATH) -> list[dict[str, Any]]:
    """Run ``sql`` read-only against the Chinook database and return row dicts."""
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        cur = con.execute(sql)
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        con.close()


class _Dumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str_representer)


def regenerate(path: pathlib.Path = GOLD_DATASET_PATH, db_path: pathlib.Path = CHINOOK_DB_PATH) -> int:
    """Re-execute every ``gold_sql`` and rewrite ``gold_result`` in place.

    Returns the number of questions whose result was written.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    written = 0
    for item in raw:
        sql = item.get("gold_sql")
        if sql:
            item["gold_sql"] = sql.strip() + "\n"
            item["gold_result"] = execute_gold_sql(sql, db_path)
            written += 1
        else:
            item["gold_result"] = None
    items = [GoldQuestion.model_validate(item).model_dump() for item in raw]
    for item in items:
        if item["paraphrase_group"] is None:
            del item["paraphrase_group"]
    text = yaml.dump(items, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120, default_flow_style=None)
    path.write_text(text.replace("\n- id:", "\n\n- id:"), encoding="utf-8", newline="\n")
    return written


if __name__ == "__main__":
    count = regenerate()
    print(f"Wrote gold_result for {count} questions to {GOLD_DATASET_PATH}")
