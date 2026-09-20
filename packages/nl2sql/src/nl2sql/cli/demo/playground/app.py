"""The playground FastAPI app.

Four routes, no state of its own:

``GET  /``            the built React page
``GET  /api/meta``    mode, dataset, the guided questions and the roles
``GET  /api/schema``  the indexed schema, so a visitor sees the database first
``POST /api/ask``     one ``QueryResult``, plus a ``replay_miss`` flag

Every pane in the browser is a renderer over ``QueryResult``; nothing is
computed here that the engine does not already return.
"""
from __future__ import annotations

import pathlib
from importlib.resources import files
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from nl2sql.auth.models import UserContext

# The errors replay mode raises when no recording matches the question. The page
# turns these into "this question has no recording" rather than a crash report.
REPLAY_MISS_CODES = {"PLANNING_FAILURE", "MISSING_LLM"}

# An unrecorded question usually fails earlier than the planner: the replay
# server answers 400 with this marker and the pipeline reports it as
# ORCHESTRATOR_CRASH, which no error code alone distinguishes from a real crash.
REPLAY_MISS_MARKER = "fake llm: no rule for"

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


def build_app(engine, questions: List[str], roles: List[str], mode: str, dataset: str) -> FastAPI:
    app = FastAPI(title="nl2sql playground")
    page = _read_page()

    if STATIC_DIR.is_dir():
        # Harmless when the build inlined everything; needed when it did not.
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return page

    @app.get("/api/meta")
    def meta() -> Dict[str, Any]:
        return {"mode": mode, "dataset": dataset, "questions": questions, "roles": roles}

    @app.get("/api/schema")
    def schema(datasource: Optional[str] = None) -> Dict[str, Any]:
        return _schema_payload(engine, datasource or _default_datasource(engine, dataset))

    @app.post("/api/ask")
    def ask(req: AskRequest) -> Dict[str, Any]:
        # Sync on purpose: the engine blocks, so Starlette runs this in a thread
        # instead of stalling the event loop.
        result = engine.run_query(
            req.question, execute=req.execute, user_context=UserContext(roles=[req.role])
        )
        body = result.model_dump(mode="json")
        errors = body.get("errors", [])
        codes = {e.get("error_code") for e in errors}
        missing = bool(codes & REPLAY_MISS_CODES) or any(
            REPLAY_MISS_MARKER in (e.get("message") or "") for e in errors
        )
        body["replay_miss"] = mode == "replay" and missing
        return body

    return app
