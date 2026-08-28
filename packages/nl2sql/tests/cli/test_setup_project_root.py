"""`nl2sql setup` writes where it is invoked.

`PROJECT_ROOT` used to walk up from the installed module, so a source
checkout resolved to the repo root and the wizard wrote its configs into the
repo instead of the user's working directory.
"""

from __future__ import annotations

import importlib
import os
import pathlib

from nl2sql.cli.commands import setup as wizard


def _reload_in(directory) -> None:
    os.chdir(directory)
    importlib.reload(wizard)


def test_project_root_follows_cwd(tmp_path):
    original = pathlib.Path.cwd()
    try:
        _reload_in(tmp_path)
        assert wizard.PROJECT_ROOT == pathlib.Path.cwd()
        assert wizard.CONFIG_DIR == pathlib.Path.cwd() / "configs"
        assert wizard.LLM_CONFIG == pathlib.Path.cwd() / "configs" / "llm.yaml"
    finally:
        _reload_in(original)


def test_project_root_ignores_an_existing_configs_dir_above_cwd(tmp_path):
    """A `configs/` directory further up the tree must not win over cwd."""
    (tmp_path / "configs").mkdir()
    nested = tmp_path / "sub" / "project"
    nested.mkdir(parents=True)

    original = pathlib.Path.cwd()
    try:
        _reload_in(nested)
        assert wizard.PROJECT_ROOT == pathlib.Path.cwd()
    finally:
        _reload_in(original)
