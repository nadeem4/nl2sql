"""The playground FastAPI app: meta, ask, schema and the served page."""
import pathlib
import re

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from nl2sql import NL2SQL
from nl2sql.api.query_api import QueryResult, SubQueryResult
from nl2sql.cli.demo.playground.app import build_app

REPLAY_MISS_MESSAGE = "No recorded answer for this question. Add an API key to ask it live."
from nl2sql.pipeline.nodes.validator.schemas import ValidationCheck
from nl2sql_adapter_sdk.schema import (
    ColumnContract,
    ColumnMetadata,
    ForeignKeyContract,
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
)


class _Engine(NL2SQL):
    """The real facade over a stub schema store; ``run_query`` is scripted."""

    def __init__(self, snapshot=None, datasources=("chinook",)):
        self.calls = []
        self._ctx = _Context(snapshot, datasources)

    def list_datasources(self):
        return self._ctx.ds_registry.list_ids()

    def run_query(self, natural_language, datasource_id=None, execute=True, user_context=None):
        self.calls.append((natural_language, execute, user_context.roles))
        return QueryResult(status="success", sub_queries=[SubQueryResult(
            id="sq1", sql="SELECT 1", status="success",
            validation=[ValidationCheck(name="policy", passed=True, message="ok")])])


class _Context:
    def __init__(self, snapshot, datasources):
        self.schema_store = _SchemaStore(snapshot)
        self.vector_store = None
        self.ds_registry = _Registry(datasources)


class _SchemaStore:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def get_latest_snapshot(self, datasource_id):
        return self._snapshot


class _Adapter:
    def __init__(self, datasource_id):
        self.datasource_id = datasource_id


class _Registry:
    def __init__(self, datasources):
        self._adapters = [_Adapter(d) for d in datasources]

    def list_ids(self):
        return [a.datasource_id for a in self._adapters]


def _snapshot() -> SchemaSnapshot:
    album = TableRef(schema_name="main", table_name="Album")
    artist = TableRef(schema_name="main", table_name="Artist")
    contract = SchemaContract(
        datasource_id="chinook",
        engine_type="sqlite",
        tables={
            album.full_name: TableContract(
                table=album,
                columns={
                    "AlbumId": ColumnContract(name="AlbumId", data_type="INTEGER",
                                              is_nullable=False, is_primary_key=True),
                    "ArtistId": ColumnContract(name="ArtistId", data_type="INTEGER",
                                               is_nullable=False),
                },
                foreign_keys=[ForeignKeyContract(constrained_columns=["ArtistId"],
                                                 referred_table=artist,
                                                 referred_columns=["ArtistId"])],
            ),
            artist.full_name: TableContract(
                table=artist,
                columns={"ArtistId": ColumnContract(name="ArtistId", data_type="INTEGER",
                                                    is_nullable=False, is_primary_key=True)},
            ),
        },
    )
    metadata = SchemaMetadata(
        datasource_id="chinook",
        engine_type="sqlite",
        tables={
            album.full_name: TableMetadata(
                table=album,
                row_count=347,
                description="One row per album.",
                columns={"AlbumId": ColumnMetadata(description="Primary key.")},
            )
        },
    )
    return SchemaSnapshot(contract=contract, metadata=metadata)


def test_meta_and_ask():
    engine = _Engine()
    client = TestClient(build_app(engine, questions=["q1"], roles=["admin", "viewer"], mode="replay", dataset="chinook"))
    assert client.get("/api/meta").json() == {
        "mode": "replay", "dataset": "chinook", "questions": ["q1"],
        "question_groups": [{"datasource": "chinook", "questions": ["q1"]}],
        "roles": ["admin", "viewer"], "datasources": ["chinook"], "recorded_questions": 0,
        # Local mode: the public-demo server is off and has no limits to report.
        "hosted": False, "limits": None}
    r = client.post("/api/ask", json={"question": "q1", "role": "viewer", "execute": False})
    assert r.status_code == 200
    body = r.json()
    assert body["sub_queries"][0]["validation"][0]["name"] == "policy" and body["replay_miss"] is False
    assert engine.calls == [("q1", False, ["viewer"])]


def test_page_has_the_four_panes():
    client = TestClient(build_app(_Engine(), questions=[], roles=["admin"], mode="live", dataset="chinook"))
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert '<div id="root">' in html
    # The React bundle owns the panes; the page only has to mount it.
    assert "/static/" in html or "<script" in html


def test_schema_route_reads_the_engine_snapshot():
    engine = _Engine(snapshot=_snapshot())
    client = TestClient(build_app(engine, questions=[], roles=["admin"], mode="replay", dataset="chinook"))
    body = client.get("/api/schema").json()

    assert body["datasource_id"] == "chinook"
    names = [t["name"] for t in body["tables"]]
    assert names == ["Album", "Artist"]

    album = body["tables"][0]
    assert album["row_count"] == 347
    assert album["description"] == "One row per album."
    assert [c["name"] for c in album["columns"]] == ["AlbumId", "ArtistId"]
    assert album["columns"][0] == {
        "name": "AlbumId",
        "type": "INTEGER",
        "nullable": False,
        "primary_key": True,
        "description": "Primary key.",
    }
    assert album["foreign_keys"] == [
        {"columns": ["ArtistId"], "references_table": "Artist", "references_columns": ["ArtistId"]}
    ]


def test_meta_lists_every_registered_datasource_with_the_demo_s_own_first():
    """The rail's switcher is built from this list, so all three have to be in it."""
    engine = _Engine(datasources=("support", "chinook", "webanalytics"))
    client = TestClient(build_app(engine, questions=[], roles=["admin"], mode="replay",
                                  dataset="chinook"))
    assert client.get("/api/meta").json()["datasources"] == ["chinook", "support", "webanalytics"]


def test_meta_lists_a_datasource_with_no_guided_questions_too():
    """A database is worth looking at whether or not a guided question asks about it."""
    client = TestClient(build_app(_Engine(datasources=("chinook", "support")), questions=["c1"],
                                  roles=["admin"], mode="replay", dataset="chinook",
                                  questions_by_datasource={"chinook": ["c1"]}))
    meta = client.get("/api/meta").json()
    assert meta["datasources"] == ["chinook", "support"]
    assert [g["datasource"] for g in meta["question_groups"]] == ["chinook"]


def test_schema_route_follows_the_datasource_the_panel_asks_for():
    engine = _Engine(snapshot=None, datasources=("chinook", "support"))
    client = TestClient(build_app(engine, questions=[], roles=["admin"], mode="replay",
                                  dataset="chinook"))
    assert client.get("/api/schema").json()["datasource_id"] == "chinook"
    assert client.get("/api/schema", params={"datasource": "support"}).json()["datasource_id"] == "support"


def test_schema_route_is_empty_when_nothing_is_indexed():
    client = TestClient(build_app(_Engine(snapshot=None), questions=[], roles=["admin"],
                                  mode="replay", dataset="chinook"))
    body = client.get("/api/schema").json()
    assert body["tables"] == []
    assert body["datasource_id"] == "chinook"


def test_ask_flags_a_replay_miss():
    class _Missing(_Engine):
        def run_query(self, natural_language, datasource_id=None, execute=True, user_context=None):
            return QueryResult(status="error", errors=[
                {"node": "ast_planner", "message": "no rule", "error_code": "PLANNING_FAILURE",
                 "severity": "ERROR"}])

    client = TestClient(build_app(_Missing(), questions=[], roles=["admin"], mode="replay", dataset="chinook"))
    body = client.post("/api/ask", json={"question": "q", "role": "admin", "execute": True}).json()
    assert body["replay_miss"] is True
    assert [e["message"] for e in body["errors"]] == [REPLAY_MISS_MESSAGE]

    live = TestClient(build_app(_Missing(), questions=[], roles=["admin"], mode="live", dataset="chinook"))
    assert live.post("/api/ask", json={"question": "q", "role": "admin"}).json()["replay_miss"] is False


def test_a_missing_recording_is_a_replay_miss_whatever_code_it_surfaces_as():
    """An unrecorded question fails in the decomposer, not the planner.

    The replay server answers 400 `fake llm: no rule for ...`, which the
    pipeline reports as ORCHESTRATOR_CRASH. Keying only on PLANNING_FAILURE and
    MISSING_LLM showed a visitor a raw crash instead of "no recording".
    """
    class _NoRule(_Engine):
        def run_query(self, natural_language, datasource_id=None, execute=True, user_context=None):
            return QueryResult(status="error", errors=[{
                "node": "decomposer",
                "message": "Decomposition failed: Error code: 400 - {'error': "
                           "{'message': 'fake llm: no rule for DecomposerResponse'}}",
                "error_code": "ORCHESTRATOR_CRASH",
                "severity": "ERROR",
            }])

    client = TestClient(build_app(_NoRule(), questions=[], roles=["admin"], mode="replay", dataset="chinook"))
    reply = client.post("/api/ask", json={"question": "q", "role": "admin"})
    assert reply.json()["replay_miss"] is True
    # A plain answer, not the replay server's internals dressed as a crash.
    assert [e["message"] for e in reply.json()["errors"]] == [REPLAY_MISS_MESSAGE]
    assert "fake llm" not in reply.text and "ORCHESTRATOR_CRASH" not in reply.text

    live = TestClient(build_app(_NoRule(), questions=[], roles=["admin"], mode="live", dataset="chinook"))
    assert live.post("/api/ask", json={"question": "q", "role": "admin"}).json()["replay_miss"] is False


def test_meta_groups_the_guided_questions_by_datasource():
    """With several databases the page can label each pile, not run them together."""
    by_datasource = {"chinook": ["c1", "c2"], "support": ["s1"]}
    client = TestClient(build_app(_Engine(), questions=["c1", "c2", "s1"], roles=["admin"],
                                  mode="replay", dataset="chinook",
                                  questions_by_datasource=by_datasource))
    meta = client.get("/api/meta").json()
    assert meta["question_groups"] == [{"datasource": "chinook", "questions": ["c1", "c2"]},
                                       {"datasource": "support", "questions": ["s1"]}]
    # The flat list stays, so nothing that reads it has to change.
    assert meta["questions"] == ["c1", "c2", "s1"]


def test_meta_drops_a_datasource_with_no_guided_questions():
    client = TestClient(build_app(_Engine(), questions=["c1"], roles=["admin"], mode="replay",
                                  dataset="chinook",
                                  questions_by_datasource={"chinook": ["c1"], "support": []}))
    assert client.get("/api/meta").json()["question_groups"] == [
        {"datasource": "chinook", "questions": ["c1"]}]


def test_meta_says_how_many_guided_questions_have_recordings():
    client = TestClient(build_app(_Engine(), questions=["q1", "q2"], roles=["admin"], mode="replay",
                                  dataset="chinook", recorded_questions=1))
    assert client.get("/api/meta").json()["recorded_questions"] == 1


# --- GET /api/trace/{trace_id} -------------------------------------------

_TRACE_ID = "0b8f7d2e-1111-4222-8333-944455556666"


def _trace_client(tmp_path):
    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / f"20260921T101112000000Z_{_TRACE_ID}.json").write_text(
        '{"trace_format_version": 1, "trace_id": "%s", "nodes": []}' % _TRACE_ID, encoding="utf-8")
    # A file that must stay unreachable: one directory up from the traces.
    (tmp_path / "20260921T101112000000Z_outside.json").write_text('{"secret": true}', encoding="utf-8")
    app = build_app(_Engine(), questions=[], roles=["admin"], mode="live", dataset="chinook", trace_dir=traces)
    return TestClient(app)


def test_trace_route_serves_a_trace_from_the_traces_directory(tmp_path):
    client = _trace_client(tmp_path)
    response = client.get(f"/api/trace/{_TRACE_ID}")
    assert response.status_code == 200
    assert response.json()["trace_id"] == _TRACE_ID


def test_trace_route_404s_an_unknown_id(tmp_path):
    client = _trace_client(tmp_path)
    assert client.get("/api/trace/does-not-exist").status_code == 404


@pytest.mark.parametrize("attack", [
    "..%2Foutside",
    "..%2F..%2Fetc%2Fpasswd",
    "%2E%2E%2Foutside",
    "..%5Coutside",
    "C:%5CWindows%5Cwin.ini",
    "%2Fetc%2Fpasswd",
    "outside.json",
    "*",
])
def test_trace_route_rejects_path_traversal(tmp_path, attack):
    client = _trace_client(tmp_path)
    response = client.get(f"/api/trace/{attack}")
    assert response.status_code in (400, 404)
    assert "secret" not in response.text


def test_trace_route_never_resolves_a_file_outside_the_directory(tmp_path):
    client = _trace_client(tmp_path)
    # "outside" is a valid-looking id, but its file sits one level up.
    assert client.get("/api/trace/outside").status_code == 404


# ---------- the link preview ----------
#
# A crawler runs no JavaScript and resolves no relative path, and both of those
# are the point here: the tags are in the HTML the server sends, and the URLs
# in them are absolute and name the host the visitor typed.

REPO = pathlib.Path(__file__).resolve().parents[4]
CARD_COPIES = (
    REPO / "docs" / "assets" / "social-card.png",
    REPO / "packages" / "nl2sql" / "src" / "nl2sql" / "cli" / "demo" / "playground" / "assets" / "social-card.png",
)


def _served_page(**headers) -> str:
    client = TestClient(build_app(_Engine(), questions=[], roles=["admin"], mode="live", dataset="chinook"))
    response = client.get("/", headers=headers)
    assert response.status_code == 200
    return response.text


def _tag(html: str, key: str) -> str:
    match = re.search(rf'<meta (?:name|property)="{re.escape(key)}" content="([^"]*)"', html)
    assert match, f"the served page has no {key} tag"
    return match.group(1)


def test_the_served_page_carries_the_preview_tags_a_crawler_reads():
    html = _served_page()

    # In the head of the document, not added to it by the bundle afterwards.
    assert html.index("og:title") < html.index('<div id="root">')
    assert _tag(html, "og:type") == "website"
    assert _tag(html, "og:title") == "nl2sql playground"
    assert "plain English" in _tag(html, "og:description")
    assert _tag(html, "twitter:card") == "summary_large_image"
    assert _tag(html, "twitter:title") == _tag(html, "og:title")
    assert _tag(html, "twitter:description") == _tag(html, "og:description")
    assert _tag(html, "twitter:image") == _tag(html, "og:image")
    # One description, and it says what the card says.
    assert _tag(html, "description") == _tag(html, "og:description")
    assert html.count('name="description"') == 1


def test_the_preview_urls_are_absolute_and_name_the_host_the_visitor_typed():
    html = _served_page(**{"x-forwarded-proto": "https",
                           "x-forwarded-host": "nadeem4nk-nl2sql-demo.hf.space"})

    assert _tag(html, "og:url") == "https://nadeem4nk-nl2sql-demo.hf.space/"
    assert _tag(html, "og:image") == "https://nadeem4nk-nl2sql-demo.hf.space/social-card.png"
    assert _tag(html, "og:image:width") == "1200"
    assert _tag(html, "og:image:height") == "630"


def test_without_a_proxy_the_preview_urls_are_the_ones_the_app_was_reached_on():
    html = _served_page()

    # TestClient asks for http://testserver/, which is what a local run looks
    # like: absolute, and right for whoever can reach this playground.
    assert _tag(html, "og:url") == "http://testserver/"
    assert _tag(html, "og:image") == "http://testserver/social-card.png"


@pytest.mark.parametrize("host", ['evil"><script>alert(1)</script>', "not a host", "", " "])
def test_a_forged_host_header_cannot_write_a_url_into_the_page(host):
    html = _served_page(**{"x-forwarded-host": host})

    assert "<script>alert(1)</script>" not in html
    assert _tag(html, "og:image").startswith("http://")
    assert _tag(html, "og:image").endswith("/social-card.png")


def test_the_app_serves_the_card_as_a_png():
    client = TestClient(build_app(_Engine(), questions=[], roles=["admin"], mode="live", dataset="chinook"))
    response = client.get("/social-card.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_the_card_the_app_serves_is_the_one_the_space_reads_from_github():
    """Two copies, one file.

    The Space's `thumbnail:` reads the copy in `docs/` over raw GitHub, so the
    Space's own card works before the Space has built; the playground serves
    the copy in the package, so a pip install has it too.
    `scripts/render_social_card.py` writes both, and a card regenerated into
    only one of them is the drift this catches.
    """
    docs, packaged = (path.read_bytes() for path in CARD_COPIES)

    assert docs == packaged
    # Small enough that an unfurler fetches it rather than giving up.
    assert len(docs) < 200 * 1024
