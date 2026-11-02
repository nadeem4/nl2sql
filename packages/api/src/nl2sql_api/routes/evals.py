"""The `evals` namespace of `/api/v1` (docs/architecture/wave_d.md, "The routes", row A4a): the suites and the scorers.
Running them is `POST /jobs/eval`. All admin only: a report holds every role's cases, plans and SQL."""
from typing import Annotated, List

from fastapi import APIRouter, Depends

from nl2sql import NL2SQL
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.models.evals import SuiteInfo

router = APIRouter(tags=["evals"], dependencies=[Depends(identity.admin)], responses=identity.ADMIN_RESPONSES)

Engine = Annotated[NL2SQL, Depends(get_engine)]


@router.get("/evals/suites", response_model=List[SuiteInfo], summary="List the eval suites")
def suites(engine: Engine):
    """The suites `evals.suites` names, in order, each with its `name`, its `path` as configured and its case
    counts (`cases` in the file, `approved` of them run). Empty when none is configured. Admin only."""
    if not engine.config.evals.suites:
        return []
    return [SuiteInfo(name=suite.name, path=path.as_posix(), cases=len(suite.cases), approved=len(suite.approved()))
            for path, suite in zip(engine.config.evals.suites, engine.evals.load())]


@router.get("/evals/scorers", response_model=List[str], summary="List the eval scorers")
def scorers(engine: Engine):
    """The registered scorer names: the built-ins and every plugin's. Admin only."""
    return engine.evals.scorers()
