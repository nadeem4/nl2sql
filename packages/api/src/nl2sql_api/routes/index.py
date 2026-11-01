"""The `index` namespace of `/api/v1` (docs/architecture/wave_d.md, "The routes", row A3): the vector index's health,
drift and retrieval inspector. Building the index is the `index` job (`POST /jobs/index`, `routes/jobs.py`)."""
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from nl2sql import NL2SQL, DriftReport, UserContext
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.errors import ERROR_RESPONSE
from nl2sql_api.models.index import InspectRequest

router = APIRouter(tags=["index"])

Engine = Annotated[NL2SQL, Depends(get_engine)]
ADMIN = {"dependencies": [Depends(identity.admin)], "responses": identity.ADMIN_RESPONSES}
_UNKNOWN = {404: {**ERROR_RESPONSE, "description": "A named datasource is not configured, or the caller cannot "
                                                     "reach it."}}


@router.get("/index", response_model=Dict[str, Any], summary="Index health", responses=identity.AUTH_RESPONSES)
def get_index(engine: Engine, user: UserContext = Depends(identity.caller)):
    """The vector index's health: `status` (`ok`, `empty`, `stale` or `missing`), entry counts by type, when it
    was built, the embedding model, one entry per registered datasource and any problems found. Read from the live
    index. A caller who is not an admin sees only the datasources their scope reaches, a failed one by its name
    without the driver's text, and no warning that names a table."""
    return engine.index.health(user_context=user)


@router.delete("/index", response_model=Dict[str, Any], summary="Clear the index", **ADMIN)
def clear_index(engine: Engine):
    """Remove every entry from the vector store. Questions need a re-index (`POST /jobs/index`) before they can be
    answered. Admin only."""
    engine.index.clear()
    return {"success": True, "message": "Index cleared successfully"}


@router.get("/index/check", response_model=List[DriftReport], summary="Check for schema drift",
            dependencies=[Depends(identity.admin)], responses={**identity.ADMIN_RESPONSES, **_UNKNOWN})
def check_index(engine: Engine, user: UserContext = Depends(identity.admin),
                datasource_id: Annotated[Optional[List[str]], Query()] = None):
    """Compare each datasource's live schema with the one its index was built from: a `DriftReport` per datasource
    (`unchanged`, `drifted`, `not_indexed` or `failed`, with the tables and columns that moved and what each touches).
    Repeat `datasource_id` to name some; leave it out for every one. Admin only: it reads every live database."""
    return engine.index.check(datasource_id, user_context=user)


@router.get("/index/inspect", response_model=Dict[str, Any], summary="Retrieval defaults",
            responses=identity.AUTH_RESPONSES)
def inspect_defaults(engine: Engine, user: UserContext = Depends(identity.caller)):
    """The defaults a retrieval run takes: `k`, `lambda_mult`, `fetch_multiplier` and the entry `types`."""
    return engine.index.inspect_defaults()


@router.post("/index/inspect", response_model=Dict[str, Any], summary="Inspect retrieval",
             responses={**identity.AUTH_RESPONSES, **_UNKNOWN})
def inspect_index(body: InspectRequest, engine: Engine, user: UserContext = Depends(identity.caller)):
    """Run retrieval for `query` as the caller and show the candidate `pool`, the `picks` and what was `dropped`.
    The pool holds only what the caller's scope reaches."""
    return engine.index.inspect(body.query, k=body.k, lambda_mult=body.lambda_mult, types=body.types,
                                datasource_id=body.datasource_id, user_context=user)
