from fastapi import Depends, Request

from nl2sql import NL2SQL
from nl2sql_api.services import (
    DatasourceService,
    QueryService,
    LLMService,
    IndexingService,
    HealthService,
)


def get_engine(request: Request) -> NL2SQL:
    return request.app.state.engine


def get_datasource_service(
    engine: NL2SQL = Depends(get_engine),
) -> DatasourceService:
    return DatasourceService(engine)


def get_query_service(
    engine: NL2SQL = Depends(get_engine),
) -> QueryService:
    return QueryService(engine)


def get_llm_service(
    engine: NL2SQL = Depends(get_engine),
) -> LLMService:
    return LLMService(engine)


def get_indexing_service(
    engine: NL2SQL = Depends(get_engine),
) -> IndexingService:
    return IndexingService(engine)


def get_health_service(
    engine: NL2SQL = Depends(get_engine),
) -> HealthService:
    return HealthService(engine)
