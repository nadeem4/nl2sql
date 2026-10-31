"""``packages/api/openapi.json`` is the committed contract; the web app calls only routes listed in it (D200).

Rewrite it after a route changes with ``python packages/api/tests/test_openapi_snapshot.py``.
"""
import json
import pathlib

from nl2sql_api.main import create_app

SNAPSHOT = pathlib.Path(__file__).resolve().parents[1] / "openapi.json"


def _schema() -> dict:
    schema = create_app().openapi()
    schema["info"] = {key: value for key, value in schema["info"].items() if key != "version"}  # moves each release
    return json.loads(json.dumps(schema))


def test_openapi_snapshot_is_current():
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert committed == _schema(), (
        "packages/api/openapi.json is stale. Rewrite it with: python packages/api/tests/test_openapi_snapshot.py")


if __name__ == "__main__":
    SNAPSHOT.write_text(json.dumps(_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {SNAPSHOT}")
