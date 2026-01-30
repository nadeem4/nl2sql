import sys
from typing import Dict, Any
from nl2sql_cli.reporting import ConsolePresenter
from nl2sql.indexing.vector_store import VectorStore
from nl2sql.datasources import DatasourceRegistry
from nl2sql.context import NL2SQLContext
from nl2sql_cli.common.decorators import handle_cli_errors
from nl2sql.indexing.orchestrator import IndexingOrchestrator

@handle_cli_errors
def run_indexing(
    ctx: NL2SQLContext,
) -> None:
    """
    Runs schema indexing for all registered datasources.

    This command clears the existing vector store and indexes
    schema chunks for each configured datasource using the
    indexing orchestrator.

    Args:
        ctx: The initialized NL2SQLContext.
    """
    presenter = ConsolePresenter()
    presenter.print_info(f"Indexing schema to: {ctx.vector_store.persist_directory}")

    adapters = ctx.ds_registry.list_adapters()
    orchestrator = IndexingOrchestrator(ctx)
    stats = []
    errors = []

    presenter.start_interactive_status("Clearing existing data...")
    orchestrator.clear_store()
    presenter.stop_interactive_status()
    presenter.print_success("Cleared existing data.")

    presenter.start_interactive_status("Indexing...")

    for adapter in adapters:
        ds_id = adapter.datasource_id

        try:
            with presenter.status_context(f"{ds_id}..."):
                schema_stats = orchestrator.index_datasource(adapter)
                stats.append(schema_stats)
        except Exception as e:
            presenter.print_error(f"Failed to index {ds_id}: {e}")
            errors.append(
                {"datasource_id": ds_id, "error": str(e)}
            )

    presenter.stop_interactive_status()
    
    if errors:
        presenter.print_table(errors, "Indexing Errors")
    
    if stats:
        presenter.print_table(stats, "Indexing Summary")

    presenter.print_success("Indexing complete.")
