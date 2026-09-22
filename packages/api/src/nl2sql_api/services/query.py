from nl2sql import NL2SQL, UserContext
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
