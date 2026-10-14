"""Thumbs-up feedback on generated answers, proposed as verified queries.

``nl2sql.feedback.proposals`` turns feedback rows into entries in
``curated.yaml``'s shape, for a person to review and commit. It never writes
``curated.yaml`` itself, and ``nl2sql curated proposals export`` refuses to.
"""
from __future__ import annotations

import pytest
import yaml
from typer.testing import CliRunner

from nl2sql.config import Curated
from nl2sql.feedback import FeedbackStore
from nl2sql.feedback.proposals import build_proposals, render_proposals, write_proposals

runner = CliRunner()


def _row(trace_id="t1", question="How many customers are in Brazil?", **overrides):
    row = {
        "trace_id": trace_id, "rating": "up", "question": question, "role": "admin", "status": "success",
        "sql": ["SELECT COUNT(*) AS n FROM Customer WHERE Country = 'Brazil'"],
        "datasource_ids": ["chinook"], "source": "generated",
    }
    row.update(overrides)
    return row


def test_a_thumbs_up_generated_answer_becomes_a_verified_query_draft():
    document, skipped = build_proposals([_row()])

    assert set(document) == {"datasources"}  # the curated section's body: no version
    [entry] = document["datasources"]["chinook"]["verified_queries"]
    assert entry["id"] == "how_many_customers_are_in_brazil"
    assert entry["questions"] == ["How many customers are in Brazil?"]
    assert entry["sql"].strip() == "SELECT COUNT(*) AS n FROM Customer WHERE Country = 'Brazil'"
    assert entry["description"].startswith("TODO")
    assert "params" not in entry
    assert sum(skipped.values()) == 0


def test_the_rendered_yaml_loads_as_a_curated_file_and_says_values_are_literal():
    document, _ = build_proposals([_row(), _row("t2", "Top 5 artists by tracks", datasource_ids=["music"],
                                                sql=["SELECT a.Name\nFROM Artist a\nLIMIT 5"])])
    text = render_proposals(document)

    assert "literal" in text and "{params}" in text
    loaded = Curated.model_validate(yaml.safe_load(text))
    assert set(loaded.datasources) == {"chinook", "music"}


@pytest.mark.parametrize("reason, overrides", [
    ("not_thumbs_up", {"rating": "down"}),
    ("verified", {"source": "verified"}),
    ("failed", {"status": "error"}),
    ("no_sql", {"sql": []}),
    ("multiple_statements", {"sql": ["SELECT 1", "SELECT 2"]}),
    ("multiple_datasources", {"datasource_ids": ["chinook", "music"]}),
    ("no_datasource", {"datasource_ids": None}),
    ("no_question", {"question": "  "}),
])
def test_each_run_that_cannot_be_a_verified_query_is_skipped_with_its_reason(reason, overrides):
    document, skipped = build_proposals([_row(**overrides)])

    assert document["datasources"] == {}
    assert skipped[reason] == 1


def test_the_same_question_is_proposed_once_from_its_newest_run():
    rows = [_row("new", sql=["SELECT 2"]), _row("old", question="  how many customers are in BRAZIL? ",
                                                 sql=["SELECT 1"])]

    document, skipped = build_proposals(rows)

    [entry] = document["datasources"]["chinook"]["verified_queries"]
    assert entry["sql"].strip() == "SELECT 2"
    assert skipped["duplicate"] == 1


def test_ids_are_slugs_and_unique_within_a_datasource():
    rows = [_row("a", "2012 revenue?"), _row("b", "2012 revenue!!")]

    document, _ = build_proposals(rows)

    ids = [e["id"] for e in document["datasources"]["chinook"]["verified_queries"]]
    assert ids == ["q_2012_revenue", "q_2012_revenue_2"]


def test_write_refuses_to_write_over_curated_yaml(tmp_path):
    curated = tmp_path / "configs" / "curated.yaml"
    curated.parent.mkdir()
    curated.write_text("version: 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="curated.yaml"):
        write_proposals("anything", tmp_path / "configs" / ".." / "configs" / "curated.yaml", protected=[curated])
    assert curated.read_text(encoding="utf-8") == "version: 1\n"

    out = tmp_path / "curated.proposals.yaml"
    write_proposals("text\n", out, protected=[curated])
    assert out.read_text(encoding="utf-8") == "text\n"


# --- nl2sql curated proposals export -----------------------------------------------------------


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A project whose nl2sql.yaml includes curated.yaml, with thumbs-up feedback in its state store."""
    from nl2sql.testing.config import make_config

    (tmp_path / "curated.yaml").write_text("datasources: {}\n", encoding="utf-8")
    make_config(tmp_path, curated={"file": "curated.yaml"})
    monkeypatch.setenv("NL2SQL_CONFIG", str(tmp_path / "nl2sql.yaml"))
    path = tmp_path / ".nl2sql" / "state.db"
    path.parent.mkdir(parents=True)
    feedback = FeedbackStore(path)
    feedback.save(_row(), rating="up", note=None)
    feedback.save(_row("t2", "Which genre sells best?", source="verified"), rating="up", note=None)
    feedback.close()
    return path


def _app():
    from nl2sql.cli.main import app
    return app


def test_export_prints_the_proposals_to_stdout(store):
    result = runner.invoke(_app(), ["curated", "proposals", "export"])

    assert result.exit_code == 0, result.output
    assert "how_many_customers_are_in_brazil" in result.stdout
    assert "which_genre_sells_best" not in result.stdout


def test_export_writes_to_out(store, tmp_path):
    out = tmp_path / "curated.proposals.yaml"

    result = runner.invoke(_app(), ["curated", "proposals", "export", "--out", str(out)])

    assert result.exit_code == 0, result.output
    loaded = Curated.model_validate(yaml.safe_load(out.read_text(encoding="utf-8")))
    assert [q.id for q in loaded.datasources["chinook"].verified_queries] == ["how_many_customers_are_in_brazil"]


def test_export_refuses_the_configs_own_files(store, tmp_path):
    for name in ("curated.yaml", "nl2sql.yaml"):
        target = tmp_path / name
        before = target.read_bytes()
        result = runner.invoke(_app(), ["curated", "proposals", "export", "--out", str(target)])
        assert result.exit_code == 2, result.output
        assert target.read_bytes() == before
