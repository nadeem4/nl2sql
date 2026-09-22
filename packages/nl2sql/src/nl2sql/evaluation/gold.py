"""The Chinook gold evaluation dataset: its model, loader and result generator.

``datasets/chinook_gold.yaml`` is hand-written except for ``gold_result`` and
``alt_gold_result``, which are always produced by running ``gold_sql`` and each
``alt_gold_sql`` against the vendored Chinook database. After editing a
question or any of its SQL, regenerate the results with::

    python -m nl2sql.evaluation.gold
"""
from __future__ import annotations

import pathlib
import sqlite3
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from nl2sql.datasets import CHINOOK_DB_PATH

GOLD_DATASET_PATH = pathlib.Path(__file__).parent / "datasets" / "chinook_gold.yaml"

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
    # Other answers a reviewer has read the rows of and accepted for the same
    # question: a full name in one column, a year labelled by its first day, a
    # different but correct reading. Each is executed by ``regenerate`` exactly
    # as ``gold_sql`` is, and is never typed by hand.
    alt_gold_sql: list[str] = []
    alt_gold_result: Optional[list[list[dict[str, Any]]]] = None

    @model_validator(mode="after")
    def _alternatives_are_generated_and_answerable(self) -> "GoldQuestion":
        results = self.alt_gold_result or []
        if len(results) != len(self.alt_gold_sql):
            raise ValueError(f"{self.id}: {len(self.alt_gold_sql)} alt_gold_sql but {len(results)} "
                             "alt_gold_result; run `python -m nl2sql.evaluation.gold`")
        if self.alt_gold_sql and self.gold_sql is None:
            raise ValueError(f"{self.id}: an unanswerable question cannot have an alternative answer")
        return self

    def answers(self) -> list[list[dict[str, Any]]]:
        """The gold answer and every reviewed alternative, gold first."""
        return [self.gold_result or [], *(self.alt_gold_result or [])]


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
    """Re-execute every ``gold_sql`` and ``alt_gold_sql`` and rewrite the results in place.

    Returns how many results were written: one per answerable question, plus
    one per alternative answer.
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
        alternatives = [alt.strip() + "\n" for alt in item.get("alt_gold_sql") or []]
        item["alt_gold_sql"] = alternatives
        item["alt_gold_result"] = [execute_gold_sql(alt, db_path) for alt in alternatives] or None
        written += len(alternatives)
    items = [GoldQuestion.model_validate(item).model_dump() for item in raw]
    for item in items:
        if item["paraphrase_group"] is None:
            del item["paraphrase_group"]
        if not item["alt_gold_sql"]:
            del item["alt_gold_sql"]
            del item["alt_gold_result"]
    text = yaml.dump(items, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120, default_flow_style=None)
    path.write_text(text.replace("\n- id:", "\n\n- id:"), encoding="utf-8", newline="\n")
    return written


if __name__ == "__main__":
    count = regenerate()
    print(f"Wrote {count} gold and alternative results to {GOLD_DATASET_PATH}")
