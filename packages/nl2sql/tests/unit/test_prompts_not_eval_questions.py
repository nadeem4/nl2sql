"""Prompts teach the task, not the evaluation: no prompt repeats a question from an evaluation suite.

A worked example in a prompt that is also a suite question teaches the model the answer and
inflates the keyed check (wave_c.md, 18.12). Examples come from a domain no suite uses.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import nl2sql
import pytest
import yaml

PROMPT_MODULES = (
    "nl2sql.pipeline.nodes.decomposer.prompts",
    "nl2sql.pipeline.nodes.schema_retriever.selector",
    "nl2sql.pipeline.nodes.ast_planner.prompts",
    "nl2sql.pipeline.nodes.answer_synthesizer.prompts",
    "nl2sql.pipeline.nodes.datasource_resolver.prompts",
    "nl2sql.pipeline.nodes.curated_matcher.prompts",
    "nl2sql.pipeline.nodes.refiner.prompts",
)
SUITES = Path(nl2sql.__file__).parent / "evaluation" / "suites"
RUN = 5
BANNED = ("top 5 films", "spent more than 45", "revenue is in payments", "9712")


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()


def _prompts() -> dict[str, str]:
    found = {}
    for name in PROMPT_MODULES:
        module = importlib.import_module(name)
        for const, value in vars(module).items():
            if const.isupper() and isinstance(value, str) and len(value) > 40:
                found[f"{name.split('.')[-2] if name.endswith('prompts') else name.split('.')[-1]}.{const}"] = value
    return found


def _questions() -> list[str]:
    questions = []
    for path in sorted(SUITES.glob("*.yaml")):
        for case in yaml.safe_load(path.read_text(encoding="utf-8")).get("cases", []):
            if case.get("question"):
                questions.append(case["question"])
    return questions


def _runs(words: list[str]) -> set[tuple[str, ...]]:
    return {tuple(words[i:i + RUN]) for i in range(len(words) - RUN + 1)}


def test_the_guard_reads_prompts_and_questions():
    assert len(_prompts()) >= 7 and len(_questions()) > 50


@pytest.mark.parametrize("name", sorted(_prompts()))
def test_no_prompt_shares_a_run_of_words_with_a_suite_question(name):
    prompt_runs = _runs(_words(_prompts()[name]))
    shared = [q for q in _questions() if _runs(_words(q)) & prompt_runs]

    assert shared == [], f"{name} repeats evaluation question(s): {shared}"


@pytest.mark.parametrize("name", sorted(_prompts()))
def test_no_prompt_carries_a_banned_eval_phrase(name):
    text = _prompts()[name].lower()

    assert [p for p in BANNED if p in text] == []