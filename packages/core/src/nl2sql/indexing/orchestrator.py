from typing import Dict

from nl2sql_sqlalchemy_adapter import SQLAlchemyAdapterProtocol
from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext
from nl2sql.indexing.chunk_builder import SchemaChunkBuilder

logger = get_logger("indexing_orchestrator")


class IndexingOrchestrator:
    """
    Orchestrates schema indexing for datasources.

    This class coordinates schema snapshot retrieval, schema version
    registration, chunk construction, and vector store refresh.
    """

    def __init__(self, ctx: NL2SQLContext):
        """
        Initializes the indexing orchestrator.

        Args:
            ctx: Initialized NL2SQLContext.
        """
        self.vector_store = ctx.vector_store
        self.schema_contract_store = ctx.schema_contract_store
        self.schema_metadata_store = ctx.schema_metadata_store
        self.config_manager = ctx.config_manager

    def clear_store(self) -> None:
        """
        Clears the vector store.
        """
        self.vector_store.clear()

    def index_datasource(
        self,
        adapter: SQLAlchemyAdapterProtocol,
    ) -> Dict[str, int]:
        """
        Indexes schema chunks for a datasource.

        Args:
            adapter: SQLAlchemy adapter for the datasource.

        Returns:
            Indexing statistics by chunk type.
        """
        schema_snapshot = adapter.fetch_schema_snapshot()

        schema_version, evicted_versions = self.schema_contract_store.register(
            schema_snapshot.contract
        )

        self.schema_metadata_store.register(
            schema_version,
            schema_snapshot.metadata,
        )

        for evicted_version in evicted_versions:
            self.schema_metadata_store.delete(
                adapter.datasource_id,
                evicted_version,
            )

        questions = self.config_manager.get_example_questions(
            adapter.datasource_id
        )

        chunk_builder = SchemaChunkBuilder(
            ds_id=adapter.datasource_id,
            schema_snapshot=schema_snapshot,
            schema_version=schema_version,
            questions=questions,
        )

        chunks = chunk_builder.build()

        return self.vector_store.refresh_schema_chunks(
            datasource_id=adapter.datasource_id,
            schema_version=schema_version,
            chunks=chunks,
            evicted_versions=evicted_versions,
        )
