"""`nl2sql demo` repairs a broken index at startup and flags old demo folders.

The incident: a demo folder had its index folder on disk and 0 entries in it.
The demo looked only at whether the folder existed, so every later start
skipped indexing and every question failed. The demo now builds the index
whenever ``engine.check()`` reports it is not ``ok`` (``missing``, ``empty``
or ``stale``, judged by its contents; see ``nl2sql.indexing.health``).
"""
from __future__ import annotations

import json
import re

import pytest

from nl2sql.cli.commands import demo as demo_cmd
from nl2sql.cli.demo import stamp

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return " ".join(_ANSI.sub("", text).split())


@pytest.fixture
def calls(monkeypatch):
    """Records the indexing runs instead of loading an embedding model."""
    seen = {"count": 0, "result": True}

    def _index(engine, **kwargs):
        seen["count"] += 1
        if not seen["result"]:
            raise SystemExit(1)  # what run_indexing does after printing why

    monkeypatch.setattr("nl2sql.cli.common.indexing.run_indexing", _index)
    return seen


@pytest.fixture
def project(tmp_path):
    """A demo folder, as `nl2sql demo` writes it with `nl2sql init --demo`."""
    directory = tmp_path / "demo"
    demo_cmd.prepare_project(directory)
    return directory


def _with_index(demo_engines, status):
    class Engine(demo_engines.cls):
        index_status = status

    demo_engines.cls = Engine


@pytest.mark.parametrize("status", ["missing", "empty", "stale"])
def test_an_index_that_is_not_ok_is_rebuilt_at_demo_startup(project, calls, capsys, demo_engines, status):
    _with_index(demo_engines, status)

    demo_cmd.start_engine(project / "nl2sql.yaml")

    assert calls["count"] == 1
    assert "indexing the schema" in _plain(capsys.readouterr().out)


def test_a_healthy_index_is_left_alone(project, calls, demo_engines):
    demo_cmd.start_engine(project / "nl2sql.yaml")

    assert calls["count"] == 0


def test_a_failed_index_is_not_fatal_by_default(project, calls, capsys, demo_engines):
    """Plain `nl2sql demo` keeps going: the playground offers Rebuild."""
    _with_index(demo_engines, "empty")
    calls["result"] = False

    engine = demo_cmd.start_engine(project / "nl2sql.yaml")

    out = _plain(capsys.readouterr().out)
    assert engine is demo_engines[0]
    assert "Indexing failed" in out and "Rebuild" in out
    assert "nl2sql --config" in out


def test_a_failed_index_is_fatal_when_one_is_required(project, calls, capsys, demo_engines):
    """`demo --record` must not record answers given against no index."""
    _with_index(demo_engines, "missing")
    calls["result"] = False

    with pytest.raises(SystemExit) as exc:
        demo_cmd.start_engine(project / "nl2sql.yaml", require_index=True)

    assert exc.value.code == 1
    assert "Nothing was recorded" in _plain(capsys.readouterr().out)


def test_a_new_demo_folder_is_stamped_with_the_engine_version(project):
    body = json.loads((project / stamp.STAMP_FILE).read_text(encoding="utf-8"))

    assert body["engine_version"] == stamp.engine_version()


def test_a_folder_without_a_stamp_is_flagged_as_older(project, capsys):
    (project / stamp.STAMP_FILE).unlink()

    demo_cmd.prepare_project(project)

    out = _plain(capsys.readouterr().out)
    assert "older version" in out
    assert "--dir" in out


def test_a_folder_stamped_by_an_older_engine_is_flagged(project, capsys):
    (project / stamp.STAMP_FILE).write_text(json.dumps({"engine_version": "0.0.1"}), encoding="utf-8")

    demo_cmd.prepare_project(project)

    out = _plain(capsys.readouterr().out)
    assert "0.0.1" in out and "--dir" in out


def test_a_folder_stamped_by_this_engine_is_not_flagged(project, capsys):
    demo_cmd.prepare_project(project)

    assert "older version" not in _plain(capsys.readouterr().out)
