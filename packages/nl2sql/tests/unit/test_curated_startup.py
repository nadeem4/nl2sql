"""``NL2SQLContext`` loads ``curated.yaml`` at startup, and a broken file stops it."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from nl2sql.common.settings import settings
from nl2sql.context import NL2SQLContext

REPO_ROOT = Path(__file__).resolve().parents[4]

QUERY = {
    "id": "book_count",
    "description": "How many books there are",
    "questions": ["How many books are there?"],
    "sql": "SELECT COUNT(*) AS books FROM Book",
}


def _context(monkeypatch, tmp_path: Path, curated) -> NL2SQLContext:
    database = tmp_path / "books.sqlite"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS Book (BookId INTEGER PRIMARY KEY, Title TEXT)")
    ds_config = tmp_path / "datasources.yaml"
    ds_config.write_text(yaml.safe_dump({"version": 1, "datasources": [{
        "id": "books", "connection": {"type": "sqlite", "database": str(database)},
    }]}), encoding="utf-8")
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text("version: 1\nproviders: []\n", encoding="utf-8")
    if curated is not None:
        curated_path = tmp_path / "curated.custom.yaml"
        curated_path.write_text(curated if isinstance(curated, str) else yaml.safe_dump(curated),
                                encoding="utf-8")
        monkeypatch.setattr(settings, "curated_config_path", str(curated_path))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "schema_store_path", str(tmp_path / "schema_store.db"))
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    return NL2SQLContext(
        ds_config_path=ds_config,
        secrets_config_path=secrets,
        llm_config_path=REPO_ROOT / "configs" / "llm.demo.yaml",
        policies_config_path=REPO_ROOT / "configs" / "policies.demo.json",
        vector_store_path=tmp_path / "vs",
    )


def test_no_curated_file_at_the_default_path_starts_with_the_feature_off(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "curated_config_path", "configs/curated.yaml")

    ctx = _context(monkeypatch, tmp_path, curated=None)

    assert ctx.curated is None


def test_a_good_curated_file_is_loaded(monkeypatch, tmp_path):
    ctx = _context(monkeypatch, tmp_path, {"datasources": {"books": {"verified_queries": [QUERY]}}})

    assert ctx.curated.datasources["books"].verified_queries[0].id == "book_count"


@pytest.mark.parametrize("broken, fragment", [
    ("datasources: [unclosed", "curated"),
    ({"datasources": {"books": {"verified_queries": [QUERY, QUERY]}}}, "book_count"),
    ({"datasources": {"bookz": {"verified_queries": [QUERY]}}}, "bookz"),
    ({"datasources": {"books": {"verified_queries": [dict(QUERY, sql="DELETE FROM Book")]}}}, "read-only"),
])
def test_a_broken_curated_file_fails_startup(monkeypatch, tmp_path, broken, fragment):
    with pytest.raises(ValueError, match=fragment):
        _context(monkeypatch, tmp_path, broken)


def test_a_configured_curated_path_that_is_missing_fails_startup(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "curated_config_path", str(tmp_path / "missing.yaml"))

    with pytest.raises(FileNotFoundError, match="missing.yaml"):
        _context(monkeypatch, tmp_path, curated=None)


def test_curated_enabled_false_skips_the_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "curated_enabled", False)

    ctx = _context(monkeypatch, tmp_path, "datasources: [unclosed")

    assert ctx.curated is None
