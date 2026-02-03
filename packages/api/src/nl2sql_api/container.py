from nl2sql import NL2SQL

from nl2sql_api.services import DatasourceService, QueryService, LLMService, IndexingService, HealthService


class Container:
    def __init__(self):
        engine = self._create_engine()

        self.engine = engine
        self.datasource = DatasourceService(engine)
        self.query = QueryService(engine)
        self.llm = LLMService(engine)
        self.indexing = IndexingService(engine)
        self.health = HealthService(engine)

    def _create_engine(self) -> NL2SQL:
        try:
            return NL2SQL()
        except FileNotFoundError:
            return NL2SQL()
