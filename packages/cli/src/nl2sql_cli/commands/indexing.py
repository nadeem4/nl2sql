import sys
from typing import Dict, Any
from nl2sql.reporting import ConsolePresenter
from nl2sql.services.vector_store import OrchestratorVectorStore
from nl2sql.datasources import DatasourceRegistry

from nl2sql_cli.common.decorators import handle_cli_errors

@handle_cli_errors
def run_indexing(
    registry: DatasourceRegistry,
    vector_store_path: str,
    vector_store: OrchestratorVectorStore,
    llm_registry: Any = None,
) -> None:
    """Runs the indexing process for schemas and examples.

    This function orchestrates the full indexing workflow:
    1. Clears existing data from the vector store.
    2. Indexes database schemas (tables, columns, foreign keys).
    3. Indexes example questions, optionally enriching them.
    4. Displays a comprehensive summary.

    Args:
        registry: The initialized DatasourceRegistry.
        vector_store_path: Path to the vector store directory.
        vector_store: The initialized vector store instance.
        llm_registry: Registry of LLMs used for semantic enrichment.
    """
    presenter = ConsolePresenter()
    presenter.print_indexing_start(vector_store_path)

    adapters = registry.list_adapters()
    stats = []

    with presenter.console.status("[bold cyan]Clearing existing data...[/bold cyan]"):
        vector_store.clear()
    presenter.print_step("[green][OK][/green] Cleared existing data.")

    presenter.console.print("\n[bold]Indexing Schemas...[/bold]")
    for adapter in adapters:
        ds_id = adapter.datasource_id

        with presenter.console.status(f"[cyan]Indexing schema: {ds_id}...[/cyan]"):
            try:
                # Use idempotent refresh
                schema_stats = vector_store.refresh_schema(adapter, datasource_id=ds_id)
                schema_stats["id"] = ds_id
                stats.append(schema_stats)

                t_count = schema_stats["tables"]
                c_count = schema_stats["columns"]
                presenter.console.print(
                    f"  [green][OK][/green] {ds_id} [dim]({t_count} Tables, {c_count} Columns)[/dim]"
                )

            except Exception as e:
                presenter.console.print(f"  [red][FAIL][/red] {ds_id} [red]Failed: {e}[/red]")
                stats.append(
                    {"id": ds_id, "tables": 0, "columns": 0, "examples": 0, "error": str(e)}
                )

    from nl2sql.common.settings import settings
    import yaml
    import pathlib

    presenter.console.print("\n[bold]Indexing Examples...[/bold]")

    total_examples = 0
    path = pathlib.Path(settings.sample_questions_path)

    if path.exists():
        try:
            examples_data = yaml.safe_load(path.read_text()) or {}

            def get_stat_entry(ds_id):
                for s in stats:
                    if s["id"] == ds_id:
                        return s
                new_s = {"id": ds_id, "tables": 0, "columns": 0, "examples": 0}
                stats.append(new_s)
                return new_s

            enricher = None
            if llm_registry:
                try:
                    from nl2sql.pipeline.nodes.semantic.node import SemanticAnalysisNode

                    enricher = SemanticAnalysisNode(llm_registry.semantic_llm())
                except Exception as e:
                    presenter.print_warning(f"Could not load SemanticNode: {e}")
            else:
                presenter.console.print(
                    "  [yellow]![/yellow] [dim]Skipping enrichment (No LLM config)[/dim]"
                )

            for ds_id, questions in examples_data.items():
                with presenter.console.status(f"[cyan]Indexing examples for {ds_id}...[/cyan]"):
                    try:
                        # Use idempotent refresh
                        count = vector_store.refresh_examples(ds_id, questions, enricher)
                        total_examples += count

                        # Update stats
                        entry = get_stat_entry(ds_id)
                        entry["examples"] = count

                        presenter.console.print(
                            f"  [green][OK][/green] {ds_id} [dim]({count} examples)[/dim]"
                        )

                    except Exception as e:
                        presenter.console.print(
                            f"  [red][FAIL][/red] {ds_id} [red]Failed: {e}[/red]"
                        )

        except Exception as e:
            presenter.console.print(f"  [red][FAIL][/red] Failed to load {path}: {e}")
    else:
        presenter.console.print(
            f"  [yellow]![/yellow] [dim]No examples file found at {path}[/dim]"
        )

    presenter.print_indexing_summary(stats)
    presenter.print_indexing_complete()
