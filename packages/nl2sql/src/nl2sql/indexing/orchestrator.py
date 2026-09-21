from __future__ import annotations

from typing import Dict, TYPE_CHECKING

from nl2sql.datasources.protocols import DatasourceAdapterProtocol
from nl2sql.common.logger import get_logger
from nl2sql.indexing.chunk_builder import SchemaChunkBuilder
from nl2sql.indexing.enrichment_service import enrich_schema_snapshot

if TYPE_CHECKING:
    from nl2sql.context import NL2SQLContext

logger = get_logger("indexing_orchestrator")


class IndexingOrchestrator:
    """
    Orchestrates schema indexing for datasources.

    This class coordinates schema snapshot retrieval, schema version
    registration, chunk construction, and vector store refresh.
    """

    def __init__(self, ctx: NL2SQLContext, enrich: bool = True):
        """
        Initializes the indexing orchestrator.

        Args:
            ctx: Initialized NL2SQLContext.
            enrich: Whether to ask the ``indexing_enrichment`` LLM for
                descriptions. Enrichment spends tokens, so the demo and the
                playground turn it on only when asked.
        """
        self.enrich = enrich
        self.vector_store = ctx.vector_store
        self.schema_store = ctx.schema_store
        self.config_manager = ctx.config_manager
        self.llm_registry = ctx.llm_registry

    def clear_store(self) -> None:
        """
        Clears the vector store.
        """
        self.vector_store.clear()

    def index_datasource(
        self,
        adapter: DatasourceAdapterProtocol,
        vector_store=None,
        switch_guard=None,
    ) -> Dict[str, int]:
        """
        Indexes schema chunks for a datasource.

        Args:
            adapter: SQLAlchemy adapter for the datasource.
            vector_store: Store to write into; defaults to the live one. A full
                rebuild passes a staging store (see ``indexing.rebuild``).
            switch_guard: Held around the switch to the new entries.

        Returns:
            Indexing statistics by chunk type.
        """
        schema_snapshot = adapter.fetch_schema_snapshot()

        questions = self.config_manager.get_example_questions(adapter.datasource_id)
        datasource_description = self.config_manager.get_datasource_description(
            adapter.datasource_id
        )

        # Best-effort: enrich_schema_snapshot owns resolving its own LLM and
        # degrades to the unenriched snapshot when none is usable, so indexing
        # never depends on an API key being present.
        if self.enrich:
            schema_snapshot, questions = enrich_schema_snapshot(
                snapshot=schema_snapshot,
                llm_registry=self.llm_registry,
                datasource_description=datasource_description,
                existing_questions=questions,
            )

        # The description lives in the datasources config, not the database,
        # so no adapter can report it. It is the operator's own words about
        # what the datasource holds and is what the resolver matches questions
        # against, so it wins over anything the adapter or enrichment wrote.
        if datasource_description and datasource_description.strip():
            metadata = schema_snapshot.metadata.model_copy(
                update={"description": datasource_description.strip()}
            )
            schema_snapshot = schema_snapshot.model_copy(update={"metadata": metadata})

        schema_version, evicted_versions = self.schema_store.register_snapshot(
            schema_snapshot
        )

        chunk_builder = SchemaChunkBuilder(
            ds_id=adapter.datasource_id,
            schema_snapshot=schema_snapshot,
            schema_version=schema_version,
            questions=questions,
        )

        chunks = chunk_builder.build()

        target = vector_store if vector_store is not None else self.vector_store
        return target.refresh_schema_chunks(
            datasource_id=adapter.datasource_id,
            schema_version=schema_version,
            chunks=chunks,
            evicted_versions=evicted_versions,
            switch_guard=switch_guard,
        )
