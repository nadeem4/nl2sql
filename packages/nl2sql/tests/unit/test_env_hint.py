"""``active_env_file`` names the file this process actually loaded.

Error messages and ``doctor`` both have to point at a real file, and the
resolution order has to match ``nl2sql.common.settings.load_settings``.
"""

from __future__ import annotations

import pytest

from nl2sql.common.env_hint import active_env_file


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("ENV_FILE_PATH", "ENV", "APP_ENV"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_to_dot_env():
    assert active_env_file() == ".env"


def test_env_name_selects_the_suffixed_file(monkeypatch):
    monkeypatch.setenv("ENV", "demo")

    assert active_env_file() == ".env.demo"


def test_explicit_path_wins_over_the_env_name(monkeypatch):
    monkeypatch.setenv("ENV", "demo")
    monkeypatch.setenv("ENV_FILE_PATH", "/etc/nl2sql/prod.env")

    assert active_env_file() == "/etc/nl2sql/prod.env"
