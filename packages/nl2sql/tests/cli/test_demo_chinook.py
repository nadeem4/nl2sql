import json
import sqlite3

import yaml
from rich.console import Console

from nl2sql.cli.demo import DemoManager
from nl2sql.configs import PolicyFileConfig

EXPECTED_TABLES = {"Album", "Artist", "Customer", "Employee", "Genre", "Invoice", "InvoiceLine",
                   "MediaType", "Playlist", "PlaylistTrack", "Track"}


def test_setup_chinook_writes_a_complete_project(tmp_path):
    DemoManager(Console(), tmp_path).setup_chinook()
    con = sqlite3.connect(tmp_path / "data" / "chinook.sqlite")
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= tables
    ds = yaml.safe_load((tmp_path / "configs" / "datasources.demo.yaml").read_text())
    assert ds["datasources"][0]["id"] == "chinook"
    policies = PolicyFileConfig.model_validate(json.loads((tmp_path / "configs" / "policies.demo.json").read_text()))
    assert set(policies.roles) == {"admin", "analyst", "viewer"}
    assert "chinook.Customer" not in policies.roles["viewer"].allowed_tables
    assert "chinook.Employee" not in policies.roles["analyst"].allowed_tables
    questions = yaml.safe_load((tmp_path / "configs" / "sample_questions.demo.yaml").read_text())
    assert len(questions["chinook"]) == 12
    env = (tmp_path / ".env.demo").read_text()
    assert "EMBEDDING_PROVIDER=local" in env
    assert (tmp_path / "configs" / "secrets.demo.yaml").exists()
