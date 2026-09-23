"""`DemoManager.setup_demo` writes a complete three-datasource demo project."""
import json
import sqlite3

import yaml
from rich.console import Console

from nl2sql.cli.demo import DemoManager
from nl2sql.cli.demo.datasets import DEMO_QUESTIONS, DEMO_QUESTIONS_BY_DATASOURCE
from nl2sql.configs import PolicyFileConfig

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


def test_setup_demo_copies_all_three_databases(tmp_path):
    DemoManager(Console(), tmp_path).setup_demo()
    for file_name, expected in EXPECTED_TABLES.items():
        assert expected <= _tables(tmp_path / "data" / file_name), file_name


def test_setup_demo_registers_all_three_datasources(tmp_path):
    DemoManager(Console(), tmp_path).setup_demo()
    ds = yaml.safe_load((tmp_path / "configs" / "datasources.demo.yaml").read_text())
    assert [d["id"] for d in ds["datasources"]] == ["chinook", "support", "webanalytics"]
    # The resolver's answerability judge reads the description, so every
    # datasource must carry one.
    assert all(d.get("description", "").strip() for d in ds["datasources"])


def test_setup_demo_writes_example_questions_per_datasource(tmp_path):
    DemoManager(Console(), tmp_path).setup_demo()
    questions = yaml.safe_load((tmp_path / "configs" / "sample_questions.demo.yaml").read_text())
    assert set(questions) == {"chinook", "support", "webanalytics"}
    assert len(questions["chinook"]) == 12
    assert questions == DEMO_QUESTIONS_BY_DATASOURCE
    assert len(DEMO_QUESTIONS) == sum(len(v) for v in questions.values())


def test_setup_demo_writes_policies_covering_every_datasource(tmp_path):
    DemoManager(Console(), tmp_path).setup_demo()
    policies = PolicyFileConfig.model_validate(
        json.loads((tmp_path / "configs" / "policies.demo.json").read_text()))
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


def test_setup_demo_writes_the_env_and_secrets_envelope(tmp_path):
    DemoManager(Console(), tmp_path).setup_demo()
    env = (tmp_path / ".env.demo").read_text()
    assert "EMBEDDING_PROVIDER=local" in env
    assert (tmp_path / "configs" / "secrets.demo.yaml").exists()
