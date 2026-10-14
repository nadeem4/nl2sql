from typing import Any, Annotated, Dict

from fastapi import APIRouter, Depends, HTTPException

from nl2sql_api import auth
from nl2sql_api.dependencies import get_indexing_service
from nl2sql_api.services import IndexingService

router = APIRouter(tags=["indexing"])

IndexingSvc = Annotated[IndexingService, Depends(get_indexing_service)]
ADMIN = {"dependencies": [Depends(auth.require_admin)], "responses": auth.ADMIN_RESPONSES}


def _failed(result: Dict[str, Any]) -> str:
    return "; ".join(f"{e['datasource_id']}: {e['error']}" for e in result["errors"]) or "nothing was indexed"


@router.post("/index/{datasource_id}", response_model=Dict[str, Any], summary="Index one datasource", **ADMIN)
def index_datasource(
    datasource_id: str,
    service: IndexingSvc
):
    """Index one datasource's schema into the vector store, beside its live entries, and switch to the new
    ones when all are written; returns the indexing stats. A failure keeps its previous entries and is a
    500 naming the reason. Admin only."""
    try:
        result = service.build([datasource_id])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to index datasource '{datasource_id}': {e}")
    if not result["ok"]:
        raise HTTPException(status_code=500,
                            detail=f"Failed to index datasource '{datasource_id}': {_failed(result)}")
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
    names each one and why, and each keeps its previous entries. Admin only."""
    try:
        result = service.build()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to index all datasources: {e}")
    return {
        "success": result["ok"],
        "indexing_results": {"stats": result["stats"], "empty": result["empty"], "errors": result["errors"]},
        "message": "Indexed every datasource" if result["ok"] else f"Indexing failed: {_failed(result)}",
    }


@router.delete("/index", response_model=Dict[str, Any], summary="Clear the index", **ADMIN)
def clear_index(
    service: IndexingSvc
):
    """Remove every entry from the vector store. Questions need a re-index before they can be answered.
    Admin only."""
    try:
        return service.clear_index()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to clear index: {str(e)}",
        )


@router.get("/index/status", response_model=Dict[str, Any], summary="Index status")
def get_index_status(
    service: IndexingSvc
):
    """The vector index's health: `status` (`ok`, `empty`, `stale` or `missing`), entry counts by
    type, when it was built, the embedding model, one entry per registered datasource and any
    problems found. Read from the live index; the same report the playground shows."""
    try:
        return service.get_index_status()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get index status: {str(e)}",
        )
