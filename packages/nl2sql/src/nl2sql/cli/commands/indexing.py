import json
import sys
from typing import Dict, List, Optional

from nl2sql.cli.reporting import ConsolePresenter
from nl2sql.context import NL2SQLContext
from nl2sql.cli.common.decorators import handle_cli_errors
from nl2sql.indexing.rebuild import rebuild_index


@handle_cli_errors
def run_indexing(
    ctx: NL2SQLContext,
    enrich: bool = True,
    datasource_ids: Optional[List[str]] = None,
    full: bool = False,
) -> None:
    """
    Rebuilds the schema index, one datasource at a time.

    Each datasource's new entries are written beside its current ones and
    switched in only when all are written (see ``nl2sql.indexing.rebuild``):
    a failure leaves that datasource's previous entries answering questions,
    and no datasource's rebuild touches another's entries.

    Args:
        ctx: The initialized NL2SQLContext.
        enrich: Ask the LLM for table and column descriptions (spends tokens).
        datasource_ids: Only these datasources; every configured one when None.
        full: Rebuild every datasource into a new collection and swap it in.
            Needed after changing the embedding model.

    Raises:
        SystemExit: With code 1 if any datasource failed to index.
    """
    presenter = ConsolePresenter()
    presenter.print_info(f"Indexing schema to: {ctx.vector_store.persist_directory}")

    tasks: Dict[str, object] = {}

    def on_datasource(ds_id: str, event: str, error) -> None:
        if event == "start":
            tasks[ds_id] = presenter.start_task_line(f"Indexing {ds_id}...")
        elif event == "done":
            presenter.finish_task_line(tasks.pop(ds_id), f"{ds_id} indexed", success=True)
        else:
            presenter.finish_task_line(tasks.pop(ds_id), f"{ds_id} failed", success=False)
            presenter.print_error(f"Failed to index {ds_id}: {error}")

    result = rebuild_index(
        ctx, enrich=enrich, datasource_ids=datasource_ids, full=full, on_datasource=on_datasource
    )
    total_adapters = len(result.stats) + len(result.empty) + len(result.errors)

    if result.errors:
        presenter.print_table(result.errors, "Indexing Errors", columns=["datasource_id", "error"])

    if result.stats:
        summary_rows = []
        total_chunks = 0
        totals_by_type: Dict[str, int] = {}

        for s in result.stats:
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

    presenter.print_info(
        f"Datasources: total={total_adapters}, succeeded={len(result.stats)}, "
        f"failed={len(result.errors)}, empty={len(result.empty)}"
    )

    if result.empty:
        presenter.print_warning(f"Datasources with empty stats: {', '.join(result.empty)}")

    if total_adapters == 0:
        presenter.print_warning(
            "No datasources are configured, so there is nothing to index. "
            "The previous index is unchanged."
        )
        sys.exit(1)

    if not result.ok:
        # Any failure is fatal, not just a total one: scripts chaining
        # `nl2sql index && nl2sql run ...` need that signal.
        kept = (
            "The new collection was not switched in; the previous index is unchanged."
            if full else
            "Each failed datasource keeps its previous index entries unchanged."
        )
        presenter.print_warning(f"Indexing completed with errors. {kept}")
        sys.exit(1)

    presenter.print_success("Indexing complete.")
