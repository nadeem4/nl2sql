"""``nl2sql storage purge``: retention by hand, through ``engine.storage.purge()``."""
from __future__ import annotations

import os
import re
import time

from typer.testing import CliRunner

from nl2sql.cli.main import app

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def test_storage_purge_deletes_old_traces_and_prints_the_counts(project_stores):
    project_stores.traces.mkdir(parents=True)
    old = project_stores.traces / "20200101T000000000000Z_old-run.json"
    new = project_stores.traces / "20990101T000000000000Z_new-run.json"
    for path in (old, new):
        path.write_text("{}", encoding="utf-8")
    then = time.time() - 30 * 86400
    os.utime(old, (then, then))

    result = runner.invoke(app, ["storage", "purge"])
    out = ANSI.sub("", result.output)

    assert result.exit_code == 0, out
    assert not old.exists() and new.exists()
    assert "traces: deleted 1 file(s) past max_age_days" in out
    assert "results: deleted 0 file(s) past max_age_days" in out
