"""The golden files the web app's node tests read: ``ApiError`` and ``Job`` (docs/architecture/wave_d.md, "Testing").

Each is a real response of this API, so a route change that makes one stale fails here, as ``openapi.json``'s test does.
Rewrite them with ``python packages/api/tests/test_web_fixtures.py``. They live in ``nl2sql-ui/src/api/fixtures/``.
"""
import json
import pathlib
import sys
import time
import tempfile
from datetime import datetime, timedelta, timezone
from itertools import count

from fastapi.testclient import TestClient

from nl2sql import UserContext
from nl2sql.testing.config import make_config
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.jobs import IndexJob, IndexResult, Jobs
from nl2sql_api.main import create_app

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "nl2sql-ui" / "src" / "api" / "fixtures"
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def build() -> dict:
    """``{file name: its JSON}``, from responses of an app with no real engine behind it."""
    ticks = count()
    with tempfile.TemporaryDirectory() as folder:
        class Engine:
            config = make_config(pathlib.Path(folder))

        app = create_app()
        app.dependency_overrides[get_engine] = lambda: Engine
        app.dependency_overrides[identity.caller] = lambda: UserContext(user_id="ann", roles=["analyst"])
        app.state.engine = Engine
        app.state.jobs = jobs = Jobs(clock=lambda: START + timedelta(seconds=next(ticks)), new_id=lambda: "job-0001-fixture-id",
                                     poll_s=0.01)
        try:
            client = TestClient(app, raise_server_exceptions=False)
            error = client.get("/api/v1/jobs/missing")
            assert error.status_code == 404
            job = jobs.submit("index", IndexJob(datasource_ids=["chinook"]), UserContext(user_id="ann"),
                              lambda ctx: (ctx.progress("chinook", 1, 1),
                                           IndexResult(ok=True, stats=[{"datasource_id": "chinook"}]))[1])
            deadline = time.monotonic() + 5
            while jobs.get(job.id, UserContext(user_id="ann")).status != "succeeded":
                assert time.monotonic() < deadline, "the fixture job did not finish"
                time.sleep(0.005)
            return {"ApiError.json": error.json(), "Job.json": client.get(f"/api/v1/jobs/{job.id}").json()}
        finally:
            jobs.close()


def _text(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def test_web_fixtures_are_current():
    for name, data in build().items():
        path = FIXTURES / name
        assert path.exists() and path.read_text(encoding="utf-8") == _text(data), (
            f"{path} is stale. Rewrite it with: python packages/api/tests/test_web_fixtures.py")


if __name__ == "__main__":
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, data in build().items():
        (FIXTURES / name).write_text(_text(data), encoding="utf-8", newline="\n")
        print(f"wrote {FIXTURES / name}", file=sys.stderr)
