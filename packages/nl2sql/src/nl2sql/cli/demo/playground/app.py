"""The playground FastAPI app.

Twelve routes:

``GET  /``                     the built React page
``GET  /api/meta``             mode, dataset, the guided questions, the roles and how
                               many guided questions have replay recordings
``GET  /api/schema``           the indexed schema, so a visitor sees the database first
``POST /api/ask``              one ``QueryResult``, plus a ``replay_miss`` flag; a miss
                               carries one plain error instead of the raw ones
``GET  /api/trace/{trace_id}`` one run trace, read only from the traces directory
``GET  /api/settings``         the settings panel: masked key, verified models, one model per node
``POST /api/settings/key``     save an API key to ``.env.demo`` and switch to live
``POST /api/settings/models``  write a model per LLM node into ``llm.demo.yaml``
``GET  /api/index``            index health (entries by type, schema version, when built)
                               and the state of a running rebuild
``POST /api/index/rebuild``    rebuild the datasource's snapshot and vector entries
``GET  /api/retrieval``        whether the Retrieval inspector is on, and its choices
``POST /api/retrieval``        one MMR search of the live index: the pool with scores,
                               the picks in order, and what was dropped

Every result pane in the browser is a renderer over ``QueryResult``; nothing
is computed here that the engine does not already return. The only state is
the settings panel's (see ``settings.py``): the current mode, and the gate that
keeps a settings change from landing under a running question; and the index
panel's (see ``index_panel.py``): the one rebuild that may be running.
"""
from __future__ import annotations

import pathlib
from importlib.resources import files
from typing import Any, Dict, List, Optional, Union

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nl2sql.auth.models import UserContext
from nl2sql.cli.demo.playground.index_panel import IndexPanel
from nl2sql.cli.demo.playground.settings import SettingsPanel
from nl2sql.common.settings import settings
from nl2sql.tracing.document import find_trace

# The errors replay mode raises when no recording matches the question. The page
# turns these into "this question has no recording" rather than a crash report.
REPLAY_MISS_CODES = {"PLANNING_FAILURE", "MISSING_LLM"}

# An unrecorded question usually fails earlier than the planner: the replay
# server answers 400 with this marker and the pipeline reports it as
# ORCHESTRATOR_CRASH, which no error code alone distinguishes from a real crash.
REPLAY_MISS_MARKER = "fake llm: no rule for"

# What a replay miss answers instead of the replay server's internals.
REPLAY_MISS_MESSAGE = "No recorded answer for this question. Add an API key to ask it live."

STATIC_DIR = pathlib.Path(str(files("nl2sql.cli.demo.playground") / "static"))

# Shown when the page has not been built. The React source lives in
# `web/playground`; `npm run build` writes into STATIC_DIR and the result is
# committed so a pip install needs no Node.
_MISSING_PAGE = (
    "<!doctype html><html><head><title>nl2sql playground</title></head>"
    "<body><div id=\"root\">The playground page has not been built. "
    "Run <code>npm install &amp;&amp; npm run build</code> in "
    "<code>web/playground</code>.</div></body></html>"
)


class AskRequest(BaseModel):
    question: str
    role: str = "admin"
    execute: bool = True


class KeyRequest(BaseModel):
    api_key: str


class NodeChoice(BaseModel):
    provider: str
    model: str


class ModelsRequest(BaseModel):
    # Per node: None (the default agent), a model on the default's provider,
    # or a provider and a model.
    models: Dict[str, Union[NodeChoice, str, None]]


class RebuildRequest(BaseModel):
    enrich: bool = False


# The entry types the index holds, in the order the inspector lists them.
ENTRY_TYPES = ["schema.datasource", "schema.table", "schema.column", "schema.relationship", "schema.metric"]


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=8, ge=1, le=50)
    lambda_mult: float = Field(default=0.7, ge=0.0, le=1.0)
    types: List[str] = Field(default_factory=list)
    datasource_id: Optional[str] = None


def _retrieval_off_reason(panel) -> Optional[str]:
    """Why the inspector is off, in its own words; the gate is the settings panel's."""
    if panel.available:
        return None
    if panel.project_dir is None:
        return "The Retrieval inspector is available in the playground that nl2sql demo starts."
    return (
        f"This playground is bound to {panel.host}, which other machines can reach, and it has no "
        "login. The inspector shows every index entry, including column statistics and sample "
        "values, to anyone who can open this page. Restart it on 127.0.0.1, or pass "
        "--allow-settings if you trust everyone who can reach it."
    )


def _read_page() -> str:
    index = STATIC_DIR / "index.html"
    try:
        return index.read_text(encoding="utf-8")
    except OSError:
        return _MISSING_PAGE


def _default_datasource(engine, dataset: str) -> str:
    """The datasource the schema panel shows when the caller names none."""
    registry = getattr(getattr(engine, "context", None), "ds_registry", None)
    adapters = registry.list_adapters() if registry is not None else []
    ids = [a.datasource_id for a in adapters]
    return dataset if dataset in ids else (ids[0] if ids else dataset)


def _columns(table_contract, table_metadata) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, column in table_contract.columns.items():
        column_metadata = table_metadata.columns.get(name) if table_metadata else None
        out.append(
            {
                "name": name,
                "type": column.data_type,
                "nullable": bool(column.is_nullable),
                "primary_key": bool(column.is_primary_key),
                "description": (column_metadata.description if column_metadata else None) or "",
            }
        )
    return out


def _foreign_keys(table_contract) -> List[Dict[str, Any]]:
    return [
        {
            "columns": list(fk.constrained_columns),
            "references_table": fk.referred_table.table_name,
            "references_columns": list(fk.referred_columns),
        }
        for fk in table_contract.foreign_keys
    ]


def _schema_payload(engine, datasource_id: str) -> Dict[str, Any]:
    """Projects the engine's own indexed snapshot onto something renderable.

    This reads the schema store the indexer wrote, not the database file: what
    the page shows is exactly what the planner was given.
    """
    store = getattr(getattr(engine, "context", None), "schema_store", None)
    snapshot = store.get_latest_snapshot(datasource_id) if store is not None else None
    if snapshot is None:
        return {"datasource_id": datasource_id, "tables": []}

    tables: List[Dict[str, Any]] = []
    for table_key, table_contract in snapshot.contract.tables.items():
        table_metadata = snapshot.metadata.tables.get(table_key)
        tables.append(
            {
                "name": table_contract.table.table_name,
                "schema": table_contract.table.schema_name,
                "row_count": table_metadata.row_count if table_metadata else None,
                "description": (table_metadata.description if table_metadata else None) or "",
                "columns": _columns(table_contract, table_metadata),
                "foreign_keys": _foreign_keys(table_contract),
            }
        )
    tables.sort(key=lambda t: t["name"])
    return {"datasource_id": datasource_id, "tables": tables}


def build_app(engine, questions: List[str], roles: List[str], mode: str, dataset: str,
              trace_dir: Optional[pathlib.Path] = None, project_dir: Optional[pathlib.Path] = None,
              host: str = "127.0.0.1", allow_settings: bool = False,
              recorded_questions: int = 0) -> FastAPI:
    """Builds the playground app over ``engine``.

    ``recorded_questions`` is how many of ``questions`` the loaded replay
    recordings can answer; ``/api/meta`` reports it so the page claims
    recorded answers only when there are some.

    ``project_dir``, ``host`` and ``allow_settings`` drive the settings panel:
    it is on only for a demo project, served on a loopback host or with
    ``allow_settings``; everywhere else it reports why it is off.
    """
    app = FastAPI(title="nl2sql playground")
    page = _read_page()
    panel = SettingsPanel(engine, project_dir, mode, host, allow_settings)
    index_panel = IndexPanel(engine, panel, _default_datasource(engine, dataset), project_dir)
    app.state.settings_panel = panel
    app.state.index_panel = index_panel

    def retrieval_guard(request: Request) -> None:
        # Local only, exactly like Settings and Rebuild: the same guard, with
        # the inspector's own reason when it is off.
        reason = _retrieval_off_reason(panel)
        if reason:
            raise HTTPException(status_code=403, detail=reason)
        panel.guard(request)

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI echoes the offending input by default; for the key route that
        # input is the key. Keep where and why, drop what.
        detail = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": detail})

    if STATIC_DIR.is_dir():
        # Harmless when the build inlined everything; needed when it did not.
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return page

    @app.get("/api/meta")
    def meta() -> Dict[str, Any]:
        return {"mode": panel.mode, "dataset": dataset, "questions": questions, "roles": roles,
                "recorded_questions": recorded_questions}

    @app.get("/api/schema")
    def schema(datasource: Optional[str] = None) -> Dict[str, Any]:
        return _schema_payload(engine, datasource or _default_datasource(engine, dataset))

    @app.post("/api/ask")
    def ask(req: AskRequest) -> Dict[str, Any]:
        # Sync on purpose: the engine blocks, so Starlette runs this in a thread
        # instead of stalling the event loop.
        with panel.gate.run():
            result = engine.run_query(
                req.question, execute=req.execute, user_context=UserContext(roles=[req.role])
            )
        body = result.model_dump(mode="json")
        errors = body.get("errors", [])
        codes = {e.get("error_code") for e in errors}
        missing = bool(codes & REPLAY_MISS_CODES) or any(
            REPLAY_MISS_MARKER in (e.get("message") or "") for e in errors
        )
        body["replay_miss"] = panel.mode == "replay" and missing
        if body["replay_miss"]:
            # The raw errors name the fake server and read as a crash; the
            # reasoning log and the failed call's usage entry repeat them.
            body["errors"] = [{"node": "replay", "message": REPLAY_MISS_MESSAGE,
                               "error_code": "REPLAY_MISS", "severity": "ERROR"}]
            body["reasoning"] = [r for r in body.get("reasoning", []) if REPLAY_MISS_MARKER not in str(r)]
            for call in (body.get("usage") or {}).get("calls", []):
                if REPLAY_MISS_MARKER in (call.get("error") or ""):
                    call["error"] = REPLAY_MISS_MESSAGE
        return body

    @app.get("/api/trace/{trace_id}")
    def trace(trace_id: str) -> FileResponse:
        """One trace file. The id is validated as a plain token and the file must
        sit directly in the traces directory, so no path can lead outside it."""
        directory = pathlib.Path(trace_dir) if trace_dir is not None else pathlib.Path(settings.trace_dir)
        try:
            path = find_trace(trace_id, directory)
        except ValueError:
            raise HTTPException(status_code=400, detail="Not a valid trace id.")
        if path is None:
            raise HTTPException(status_code=404, detail="No trace with that id.")
        return FileResponse(path, media_type="application/json", filename=path.name,
                            content_disposition_type="inline")

    @app.get("/api/settings")
    def read_settings() -> Dict[str, Any]:
        return panel.read()

    @app.post("/api/settings/key", dependencies=[Depends(panel.guard)])
    def save_key(req: KeyRequest) -> Dict[str, Any]:
        # Sync, like /api/ask: the save waits for running questions to finish.
        panel.save_key(req.api_key)
        return panel.read()

    @app.post("/api/settings/models", dependencies=[Depends(panel.guard)])
    def save_models(req: ModelsRequest) -> Dict[str, Any]:
        panel.set_models({agent: value.model_dump() if isinstance(value, NodeChoice) else value
                          for agent, value in req.models.items()})
        return panel.read()

    @app.get("/api/index")
    def read_index() -> Dict[str, Any]:
        return index_panel.read()

    @app.post("/api/index/rebuild", status_code=202, dependencies=[Depends(panel.guard)])
    def rebuild(req: RebuildRequest) -> Dict[str, Any]:
        # Guarded exactly like a settings change: local only unless
        # --allow-settings, and only from the playground page itself.
        index_panel.start(req.enrich)
        return index_panel.read()

    @app.get("/api/retrieval")
    def retrieval_options() -> Dict[str, Any]:
        from nl2sql.indexing.vector_store import VectorStore

        return {
            "available": panel.available,
            "reason": _retrieval_off_reason(panel),
            "datasource_id": index_panel.datasource_id,
            "datasources": [d["datasource_id"] for d in index_panel.health().get("datasources", [])],
            "types": ENTRY_TYPES,
            "defaults": {"k": 8, "lambda_mult": VectorStore.LAMBDA_MULT,
                         "fetch_multiplier": VectorStore.FETCH_MULTIPLIER},
        }

    @app.post("/api/retrieval", dependencies=[Depends(retrieval_guard)])
    def retrieval(req: RetrievalRequest) -> Dict[str, Any]:
        """One MMR search of the live index, as the engine runs it. It reads index
        metadata only; the embedder is local, so it costs nothing."""
        unknown = sorted(set(req.types) - set(ENTRY_TYPES))
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown entry types: {', '.join(unknown)}.")
        store = getattr(getattr(engine, "context", None), "vector_store", None)
        if store is None:
            raise HTTPException(status_code=409, detail="This playground has no vector index configured.")
        try:
            return store.inspect(req.query, k=req.k, lambda_mult=req.lambda_mult,
                                 types=req.types, datasource_id=req.datasource_id or None)
        except Exception as exc:  # a model mismatch, an unreadable collection
            raise HTTPException(status_code=409, detail=str(exc))

    return app
