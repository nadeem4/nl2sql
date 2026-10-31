from typing import Any, Annotated, Dict

import logging

from fastapi import APIRouter, Depends

from nl2sql_api import identity
from nl2sql_api.errors import RequestRefused
from nl2sql_api.dependencies import get_indexing_service
from nl2sql_api.services import IndexingService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["indexing"])

IndexingSvc = Annotated[IndexingService, Depends(get_indexing_service)]
ADMIN = {"dependencies": [Depends(identity.admin)], "responses": identity.ADMIN_RESPONSES}


def _index_failed(message: str, result: Dict[str, Any]) -> RequestRefused:
    """The per-datasource reasons are in the server log, not the response: they can carry a connection string."""
    for e in result["errors"]:
        logger.error("Indexing %s failed: %s", e["datasource_id"], e["error"])
    return RequestRefused(500, "INDEX_FAILED", message, fix="The cause is in the server log.")


@router.post("/index/{datasource_id}", response_model=Dict[str, Any], summary="Index one datasource", **ADMIN)
def index_datasource(
    datasource_id: str,
    service: IndexingSvc
):
    """Index one datasource's schema into the vector store, beside its live entries, and switch to the new
    ones when all are written; returns the indexing stats. A failure keeps its previous entries and is a
    500 `INDEX_FAILED` (the reason is in the server log). Admin only."""
    result = service.build([datasource_id])
    if not result["ok"]:
        raise _index_failed(f"Failed to index datasource '{datasource_id}'.", result)
    return {
        "success": True,
        "datasource_id": datasource_id,
        "indexing_stats": result["stats"][0] if result["stats"] else {},
        "message": f"Successfully indexed datasource '{datasource_id}'",
    }


@router.post("/index-all", response_model=Dict[str, Any], summary="Index every datasource", **ADMIN)
def index_all_datasources(
    service: IndexingSvc
):
    """Index every configured datasource, one at a time. `success` is false when any failed: `errors`
    names each one, and each keeps its previous entries. Admin only."""
    result = service.build()
    if not result["ok"]:
        for e in result["errors"]:
            logger.error("Indexing %s failed: %s", e["datasource_id"], e["error"])
    return {
        "success": result["ok"],
        "indexing_results": {"stats": result["stats"], "empty": result["empty"],
                             "errors": [{"datasource_id": e["datasource_id"]} for e in result["errors"]]},
        "message": "Indexed every datasource" if result["ok"] else "Indexing failed; the cause is in the server log.",
    }


@router.delete("/index", response_model=Dict[str, Any], summary="Clear the index", **ADMIN)
def clear_index(
    service: IndexingSvc
):
    """Remove every entry from the vector store. Questions need a re-index before they can be answered.
    Admin only."""
    return service.clear_index()


@router.get("/index/status", response_model=Dict[str, Any], summary="Index status")
def get_index_status(
    service: IndexingSvc
):
    """The vector index's health: `status` (`ok`, `empty`, `stale` or `missing`), entry counts by
    type, when it was built, the embedding model, one entry per registered datasource and any
    problems found. Read from the live index; the same report the playground shows."""
    return service.get_index_status()
