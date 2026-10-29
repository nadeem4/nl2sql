"""Example questions in the review queue (docs/architecture/wave_c.md, section 7, need B2).

``engine.proposals.propose_examples`` files each question as an ``example`` proposal (origin ``template``); only an
accept appends it to the glossary's ``examples``. A real engine on a small SQLite file with a fake embedder.
"""
from __future__ import annotations

import sqlite3

import pytest
import yaml
from langchain_core.embeddings import DeterministicFakeEmbedding

from nl2sql import NL2SQL, Embeddings, ReviewError, UserContext
from nl2sql.review.models import ReviewErrorCode
from nl2sql.testing.config import make_config

ADMIN = UserContext(user_id="ada", roles=["admin"])
ANALYST = UserContext(user_id="bob", roles=["analyst"])
ROLES = {"admin": {"description": "Everything", "allow": ["*"]},
         "analyst": {"description": "Reads", "allow": ["shop"]}}
QUESTIONS = ["How many orders are there?", "Which customers order the most?"]


@pytest.fixture
def project(tmp_path):
    with sqlite3.connect(tmp_path / "shop.db") as connection:
        connection.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, total REAL)")
    connection.close()
    (tmp_path / "glossary.yaml").write_text(yaml.safe_dump({"version": 1, "datasources": {}}), encoding="utf-8")
    config = make_config(tmp_path, datasources=[{"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}}],
                         policies={"roles": ROLES}, knowledge=[{"type": "yaml", "path": "glossary.yaml"}])
    embeddings = Embeddings(instance=DeterministicFakeEmbedding(size=8), dimension=8)
    with NL2SQL(config.model_copy(update={"embeddings": embeddings})) as engine:
        yield engine, tmp_path


def _examples(folder) -> list:
    document = yaml.safe_load((folder / "glossary.yaml").read_text(encoding="utf-8"))
    return document["datasources"].get("shop", {}).get("examples", [])


def test_propose_examples_files_one_example_proposal_per_question(project):
    engine, _ = project

    filed = engine.proposals.propose_examples("shop", QUESTIONS, by=ADMIN)

    assert [(p.kind, p.origin, p.target, p.status) for p in filed] == [
        ("example", "template", question, "proposed") for question in QUESTIONS]
    assert {p.id for p in engine.proposals.list(user_context=ADMIN, kind="example")} == {p.id for p in filed}


def test_an_example_reaches_the_glossary_only_when_accepted(project):
    engine, folder = project
    [first, _] = engine.proposals.propose_examples("shop", QUESTIONS, by=ADMIN)
    assert _examples(folder) == []

    report = engine.proposals.accept(first.id, by=ADMIN)

    assert [p.id for p in report.accepted] == [first.id] and not report.refused
    assert _examples(folder) == [QUESTIONS[0]]


def test_an_accepted_example_is_not_filed_again(project):
    engine, folder = project
    [first, _] = engine.proposals.propose_examples("shop", QUESTIONS, by=ADMIN)
    engine.proposals.accept(first.id, by=ADMIN)

    engine.proposals.propose_examples("shop", QUESTIONS, by=ADMIN)

    open_examples = engine.proposals.list(user_context=ADMIN, kind="example")
    assert [p.target for p in open_examples] == [QUESTIONS[1]]
    assert _examples(folder) == [QUESTIONS[0]]


def test_filing_examples_needs_an_admin(project):
    engine, _ = project

    with pytest.raises(ReviewError) as error:
        engine.proposals.propose_examples("shop", QUESTIONS, by=ANALYST)

    assert error.value.code == ReviewErrorCode.NOT_AUTHORIZED
    assert engine.proposals.list(user_context=ADMIN, kind="example") == []


def test_an_unknown_datasource_is_refused(project):
    engine, _ = project

    with pytest.raises(ValueError, match="Unknown datasource"):
        engine.proposals.propose_examples("nope", QUESTIONS, by=ADMIN)
