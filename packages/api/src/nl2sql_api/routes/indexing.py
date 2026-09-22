from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, Annotated

from nl2sql_api.dependencies import get_indexing_service
from nl2sql_api.services import IndexingService
router = APIRouter(tags=["indexing"])

IndexingSvc = Annotated[IndexingService, Depends(get_indexing_service)]

@router.post("/index/{datasource_id}", response_model=Dict[str, Any], summary="Index one datasource")
def index_datasource(
    datasource_id: str,
    service: IndexingSvc
):
    """Index one datasource's schema into the vector store; returns the indexing stats."""
    try:
        result = service.index_datasource(datasource_id)

        return {
            "success": True,
            "datasource_id": datasource_id,
            "indexing_stats": result,
            "message": f"Successfully indexed datasource '{datasource_id}'",
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to index datasource '{datasource_id}': {str(e)}",
        )


@router.post("/index-all", response_model=Dict[str, Any], summary="Index every datasource")
def index_all_datasources(
    service: IndexingSvc
):
    """Index every registered datasource; returns the stats per datasource."""
    try:
        results = service.index_all_datasources()

        return {
            "success": True,
            "indexing_results": results,
            "message": "Successfully initiated indexing for all datasources",
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to index all datasources: {str(e)}",
        )


@router.delete("/index", response_model=Dict[str, Any], summary="Clear the index")
def clear_index(
    service: IndexingSvc
):
    """Remove every entry from the vector store. Questions need a re-index before they can be answered."""
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
