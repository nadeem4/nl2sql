from fastapi import APIRouter, HTTPException, Request
from typing import Dict, Any

router = APIRouter()


@router.post("/index/{datasource_id}", response_model=Dict[str, Any])
async def index_datasource(request: Request, datasource_id: str):
    try:
        service = request.app.state.container.indexing
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


@router.post("/index-all", response_model=Dict[str, Any])
async def index_all_datasources(request: Request):
    try:
        service = request.app.state.container.indexing
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


@router.delete("/index", response_model=Dict[str, Any])
async def clear_index(request: Request):
    try:
        service = request.app.state.container.indexing
        return service.clear_index()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to clear index: {str(e)}",
        )


@router.get("/index/status", response_model=Dict[str, Any])
async def get_index_status(request: Request):
    try:
        service = request.app.state.container.indexing
        return service.get_index_status()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get index status: {str(e)}",
        )
