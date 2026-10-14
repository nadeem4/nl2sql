"""``nl2sql demo`` writes its project with ``nl2sql init --demo``: a complete three-datasource project."""
import sqlite3

from typer.testing import CliRunner

from nl2sql.cli.commands.demo import prepare_project
from nl2sql.cli.demo.stamp import read_stamp
from nl2sql.cli.main import app
from nl2sql.config import load_config
from nl2sql.datasets.demo import DEMO_QUESTIONS, DEMO_QUESTIONS_BY_DATASOURCE

EXPECTED_TABLES = {
    "chinook.sqlite": {"Album", "Artist", "Customer", "Employee", "Genre", "Invoice",
                       "InvoiceLine", "MediaType", "Playlist", "PlaylistTrack", "Track"},
    "support.sqlite": {"agents", "customers", "tickets", "ticket_messages"},
    "webanalytics.sqlite": {"devices", "referrers", "sessions", "page_views", "conversions"},
}


def _tables(path):
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def test_the_demo_project_is_init_demo_and_stamped(tmp_path):
    config_path = prepare_project(tmp_path)

    assert config_path == tmp_path / "nl2sql.yaml"
    assert read_stamp(tmp_path)
    for file_name, expected in EXPECTED_TABLES.items():
        assert expected <= _tables(tmp_path / "data" / file_name), file_name
    assert not (tmp_path / "configs").exists() and not (tmp_path / ".env.demo").exists()


def test_a_second_start_keeps_the_project_as_it_is(tmp_path):
    prepare_project(tmp_path)
    llm = tmp_path / "llm.yaml"
    llm.write_text(llm.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")

    prepare_project(tmp_path)

    assert llm.read_text(encoding="utf-8").endswith("# edited\n")


def test_a_folder_with_files_in_the_way_is_refused_cleanly(tmp_path):
    """A 0.4 demo folder has data/*.sqlite but no nl2sql.yaml; init --demo would overwrite them."""
    import pytest

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "chinook.sqlite").write_bytes(b"old")

    with pytest.raises(SystemExit) as exit_info:
        prepare_project(tmp_path)

    assert exit_info.value.code == 1
    assert (tmp_path / "data" / "chinook.sqlite").read_bytes() == b"old"


def test_the_demo_config_has_three_datasources_with_their_questions(tmp_path):
    config = load_config(prepare_project(tmp_path))

    assert [d.id for d in config.datasources] == ["chinook", "support", "webanalytics"]
    # The resolver's answerability judge reads the description, so every
    # datasource must carry one.
    assert all((d.description or "").strip() for d in config.datasources)
    assert {d.id: d.sample_questions for d in config.datasources} == DEMO_QUESTIONS_BY_DATASOURCE
    assert len(DEMO_QUESTIONS) == sum(len(d.sample_questions) for d in config.datasources)


def test_the_demo_policies_cover_every_datasource(tmp_path):
    policies = load_config(prepare_project(tmp_path)).policies
    assert set(policies.roles) == {"admin", "analyst", "viewer"}

    admin = policies.roles["admin"]
    assert admin.allowed_datasources == ["*"] and admin.allowed_tables == ["*"]

    # The analyst reads no employee or support-agent records.
    analyst = policies.roles["analyst"].allowed_tables
    assert "chinook.Employee" not in analyst
    assert "support.agents" not in analyst
    assert {"chinook.Customer", "support.tickets", "webanalytics.sessions"} <= set(analyst)

    # The viewer reads no customer, invoice or ticket-message data.
    viewer = policies.roles["viewer"]
    assert "support" not in viewer.allowed_datasources
    for forbidden in ("chinook.Customer", "chinook.Invoice", "chinook.InvoiceLine",
                      "support.ticket_messages", "support.tickets",
                      "webanalytics.sessions", "webanalytics.conversions"):
        assert forbidden not in viewer.allowed_tables, forbidden
    assert {"chinook.Track", "webanalytics.page_views"} <= set(viewer.allowed_tables)


def test_the_demo_verified_queries_pass_the_checks_and_run(tmp_path):
    config_path = prepare_project(tmp_path)
    queries = load_config(config_path).curated.datasources["chinook"].verified_queries
    assert 4 <= len(queries) <= 6
    for query in queries:
        # Several phrasings each, so a reworded question still finds its query.
        assert len(query.questions) >= 3, query.id
        for name, spec in query.params.items():
            assert spec.default is not None or spec.example is not None, (query.id, name)

    # Every one passes the startup checks and runs against the demo's Chinook,
    # whose path is relative to nl2sql.yaml, from any working directory.
    runner = CliRunner()
    validated = runner.invoke(app, ["--config", str(config_path), "curated", "validate"])
    tested = runner.invoke(app, ["--config", str(config_path), "curated", "test"])
    assert validated.exit_code == 0, validated.output
    assert tested.exit_code == 0, tested.output
    assert f"{len(queries)} verified queries ran" in tested.output
