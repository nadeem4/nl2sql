"""Prompts teach the task, not the evaluation: no prompt repeats a question from an evaluation suite.

A worked example in a prompt that is also a suite question teaches the model the answer and
inflates the keyed check (wave_c.md, 18.12). Examples come from a domain no suite uses.

Every ``prompts.py`` (and ``prompts/`` module) under ``nl2sql``, plus the table selector, is
checked. The 5-word check catches a copied question, not a paraphrase of one; ``BANNED`` lists
phrases that overlapped in the past, so they cannot come back.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import nl2sql
import pytest
import yaml

ROOT = Path(nl2sql.__file__).parent
SUITES = ROOT / "evaluation" / "suites"
RUN = 5
BANNED = ("top 5 films", "spent more than 45", "revenue is in payments", "9712")


def _module_names() -> list[str]:
    paths = [*ROOT.rglob("prompts.py"), *(p for d in ROOT.rglob("prompts") if d.is_dir() for p in d.rglob("*.py")),
             ROOT / "pipeline" / "nodes" / "schema_retriever" / "selector.py"]
    names = {".".join(("nl2sql", *p.relative_to(ROOT).with_suffix("").parts)).removesuffix(".__init__") for p in paths}
    return sorted(names)


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()


def _text(value: object) -> str:
    """A prompt constant's text: a string, or the template text of a LangChain prompt."""
    if isinstance(value, str):
        return value
    template = getattr(value, "template", None)
    if isinstance(template, str):
        return template
    return "\n".join(_text(getattr(m, "prompt", m)) for m in getattr(value, "messages", ()))


def _constants(name: str) -> dict[str, str]:
    module = importlib.import_module(name)
    texts = {c: _text(v) for c, v in vars(module).items() if c.isupper() and not c.startswith("_")}
    return {c: t for c, t in texts.items() if len(t) > 40}


def _prompts() -> dict[str, str]:
    return {f"{name}.{c}": v for name in _module_names() for c, v in _constants(name).items()}


def _questions() -> list[str]:
    questions = []
    for path in sorted(SUITES.glob("*.yaml")):
        for case in yaml.safe_load(path.read_text(encoding="utf-8")).get("cases", []):
            if case.get("question"):
                questions.append(case["question"])
    return questions


def _runs(words: list[str]) -> set[tuple[str, ...]]:
    return {tuple(words[i:i + RUN]) for i in range(len(words) - RUN + 1)}


def test_the_guard_finds_the_prompt_modules_and_the_questions():
    names = _module_names()

    assert "nl2sql.conversations.prompts" in names and "nl2sql.profiler.prompts" in names
    assert "nl2sql.pipeline.nodes.schema_retriever.selector" in names
    assert len(_questions()) > 50


@pytest.mark.parametrize("name", _module_names())
def test_every_prompt_module_contributes_a_string_constant(name):
    assert _constants(name), f"{name} has no prompt text for the guard to check"


@pytest.mark.parametrize("name", sorted(_prompts()))
def test_no_prompt_shares_a_run_of_words_with_a_suite_question(name):
    prompt_runs = _runs(_words(_prompts()[name]))
    shared = [q for q in _questions() if _runs(_words(q)) & prompt_runs]

    assert shared == [], f"{name} repeats evaluation question(s): {shared}"


@pytest.mark.parametrize("name", sorted(_prompts()))
def test_no_prompt_carries_a_banned_eval_phrase(name):
    text = _prompts()[name].lower()

    assert [p for p in BANNED if p in text] == []
