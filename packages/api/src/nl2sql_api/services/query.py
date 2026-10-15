from typing import Iterator

from nl2sql import NL2SQL, CancellationToken, Event, ResultPage, UserContext
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

    def stream_query(self, request: QueryRequest, user_context: UserContext,
                     cancellation_token: CancellationToken) -> Iterator[Event]:
        """The run as events; ``run.finished``'s ``data.result`` is exactly what :meth:`execute_query` returns."""
        for event in self.engine.stream_query(
            request.natural_language,
            datasource_id=request.datasource_id,
            execute=request.execute,
            user_context=user_context,
            cancellation_token=cancellation_token,
        ):
            if event.name == "run.finished":
                response = QueryResponse.model_validate(event.data["result"]).model_dump(mode="json")
                event = event.model_copy(update={"data": {**event.data, "result": response}})
            yield event

    def get_result_page(self, request_id: str, result_id: str, offset: int, limit: int) -> ResultPage:
        return self.engine.results.page(request_id, result_id, offset=offset, limit=limit)

    def export_result_csv(self, request_id: str, result_id: str) -> Iterator[bytes]:
        return self.engine.results.csv(request_id, result_id)
