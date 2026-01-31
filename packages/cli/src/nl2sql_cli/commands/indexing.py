import json
import sys
from typing import Any, Dict
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
    empty_stats = []

    presenter.start_interactive_status("Clearing existing data...")
    try:
        orchestrator.clear_store()
    except Exception as e:
        presenter.stop_interactive_status()
        presenter.print_error(f"Failed to clear existing data: {e}")
        sys.exit(1)
    presenter.stop_interactive_status()
    presenter.print_success("Cleared existing data.")

    for adapter in adapters:
        ds_id = adapter.datasource_id

        try:
            task = presenter.start_task_line(f"Indexing {ds_id}...")
            schema_stats = orchestrator.index_datasource(adapter)
            if schema_stats:
                stats.append(schema_stats)
            else:
                empty_stats.append(ds_id)
            presenter.finish_task_line(task, f"{ds_id} indexed", success=True)
        except Exception as e:
            if "task" in locals():
                presenter.finish_task_line(task, f"{ds_id} failed", success=False)
            presenter.print_error(f"Failed to index {ds_id}: {e}")
            errors.append(
                {"datasource_id": ds_id, "error": str(e)}
            )
    
    if errors:
        presenter.print_table(errors, "Indexing Errors", columns=["datasource_id", "error"])
    
    if stats:
        summary_rows = []
        total_chunks = 0
        totals_by_type: Dict[str, int] = {}

        for s in stats:
            ds_id = s.get("datasource_id", "unknown")
            schema_version = s.get("schema_version", "-")
            chunk_stats = {
                k: v for k, v in s.items()
                if k not in ("datasource_id", "schema_version")
            }
            ds_total = sum(v for v in chunk_stats.values() if isinstance(v, int))
            total_chunks += ds_total
            for key, val in chunk_stats.items():
                if isinstance(val, int):
                    totals_by_type[key] = totals_by_type.get(key, 0) + val

            summary_rows.append(
                {
                    "datasource_id": ds_id,
                    "schema_version": schema_version,
                    "total_chunks": ds_total,
                    "chunks": json.dumps(chunk_stats, separators=(",", ":")),
                }
            )

        presenter.print_table(
            summary_rows,
            "Indexing Summary",
            columns=["datasource_id", "schema_version", "total_chunks", "chunks"],
        )

        if totals_by_type:
            totals_str = ", ".join([f"{k}={v}" for k, v in sorted(totals_by_type.items())])
            presenter.print_info(f"Total chunks indexed: {total_chunks} ({totals_str})")

    total_adapters = len(adapters)
    succeeded = len(stats)
    failed = len(errors)
    skipped = len(empty_stats)
    presenter.print_info(
        f"Datasources: total={total_adapters}, succeeded={succeeded}, failed={failed}, empty={skipped}"
    )

    if empty_stats:
        presenter.print_warning(f"Datasources with empty stats: {', '.join(empty_stats)}")

    if errors:
        presenter.print_warning("Indexing completed with errors.")
    else:
        presenter.print_success("Indexing complete.")
