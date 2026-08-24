from nl2sql import NL2SQL
from nl2sql_api.models.query import QueryRequest, QueryResponse
from nl2sql.auth.models import UserContext


class QueryService:
    def __init__(self, engine: NL2SQL):
        self.engine = engine

    def execute_query(self, request: QueryRequest) -> QueryResponse:
        # Convert user_context if provided
        user_context = None
        if request.user_context:
            user_context = UserContext(**request.user_context)

        result = self.engine.run_query(
            request.natural_language,
            datasource_id=request.datasource_id,
            execute=request.execute,
            user_context=user_context,
        )

        return QueryResponse(
            sub_queries=[sub_query.model_dump() for sub_query in result.sub_queries],
            final_answer=result.final_answer,
            errors=result.errors,
            trace_id=result.trace_id,
            reasoning=result.reasoning,
            warnings=result.warnings,
            artifact_refs={
                node_id: ref.model_dump(mode="json")
                for node_id, ref in result.artifact_refs.items()
            },
        )
