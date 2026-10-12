from typing import Iterator

from nl2sql import NL2SQL, ResultPage, UserContext
from nl2sql_api.models.query import QueryRequest, QueryResponse


class QueryService:
    def __init__(self, engine: NL2SQL):
        self.engine = engine

    def execute_query(self, request: QueryRequest, user_context: UserContext) -> QueryResponse:
        """Run the question as ``user_context``, which the auth dependency supplies."""
        result = self.engine.run_query(
            request.natural_language,
            datasource_id=request.datasource_id,
            execute=request.execute,
            user_context=user_context,
        )
        return QueryResponse.model_validate(result.model_dump())

    def get_result_page(self, request_id: str, result_id: str, offset: int, limit: int) -> ResultPage:
        return self.engine.get_result_page(request_id, result_id, offset=offset, limit=limit)

    def export_result_csv(self, request_id: str, result_id: str) -> Iterator[bytes]:
        return self.engine.export_result_csv(request_id, result_id)
