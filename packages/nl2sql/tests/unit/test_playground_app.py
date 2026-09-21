"""The playground FastAPI app: meta, ask, schema and the served page."""
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

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


class _Engine:
    def __init__(self, snapshot=None, datasources=("chinook",)):
        self.calls = []
        self.context = _Context(snapshot, datasources)

    def run_query(self, natural_language, datasource_id=None, execute=True, user_context=None):
        self.calls.append((natural_language, execute, user_context.roles))
        return QueryResult(status="success", sub_queries=[SubQueryResult(
            id="sq1", sql="SELECT 1", status="success",
            validation=[ValidationCheck(name="policy", passed=True, message="ok")])])


class _Context:
    def __init__(self, snapshot, datasources):
        self.schema_store = _SchemaStore(snapshot)
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

    def list_adapters(self):
        return list(self._adapters)


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
    assert client.get("/api/meta").json() == {"mode": "replay", "dataset": "chinook", "questions": ["q1"],
                                              "roles": ["admin", "viewer"], "recorded_questions": 0}
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
