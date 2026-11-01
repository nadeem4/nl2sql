from fastapi import Depends, Request

from nl2sql import NL2SQL
from nl2sql_api.jobs import Jobs
from nl2sql_api.services import (
    QueryService,
    LLMService,
    HealthService,
)


def get_engine(request: Request) -> NL2SQL:
    return request.app.state.engine


def get_jobs(request: Request) -> Jobs:
    return request.app.state.jobs


def get_query_service(
    engine: NL2SQL = Depends(get_engine),
) -> QueryService:
    return QueryService(engine)


def get_llm_service(
    engine: NL2SQL = Depends(get_engine),
) -> LLMService:
    return LLMService(engine)


def get_health_service(
    engine: NL2SQL = Depends(get_engine),
) -> HealthService:
    return HealthService(engine)
