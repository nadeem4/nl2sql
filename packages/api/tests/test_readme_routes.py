"""The README's REST API table lists every route the app serves."""
import pathlib

from nl2sql_api.main import app

REPO = pathlib.Path(__file__).resolve().parents[3]


def test_every_route_is_in_the_readme_api_table():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    start = text.index("\n## REST API\n")
    section = text[start:text.index("\n## ", start + 1)]
    operations = [(method.upper(), path) for path, ops in app.openapi()["paths"].items() for method in ops]
    assert len(operations) == 14
    missing = [f"{m} {p}" for m, p in operations if f"| `{m}` | `{p}` |" not in section]
    assert not missing, f"add these routes to the README's REST API table: {missing}"
