"""The docs' REST route table (docs/api/rest/routes.md) lists every route the app serves."""
import pathlib

from nl2sql_api.main import app

REPO = pathlib.Path(__file__).resolve().parents[3]


def test_every_route_is_in_the_docs_route_table():
    section = (REPO / "docs" / "api" / "rest" / "routes.md").read_text(encoding="utf-8")
    operations = [(method.upper(), path) for path, ops in app.openapi()["paths"].items() for method in ops]
    assert len(operations) == 95
    missing = [f"{m} {p}" for m, p in operations if f"| `{m}` | `{p}` |" not in section]
    assert not missing, f"add these routes to docs/api/rest/routes.md: {missing}"
