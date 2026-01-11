import sys
import json
from typing import Optional

from nl2sql.datasources import DatasourceRegistry
from nl2sql.services.llm import LLMRegistry
from nl2sql.services.vector_store import OrchestratorVectorStore
# Updated Import
from nl2sql_cli.commands.visualize import draw_execution_trace
from nl2sql.reporting import ConsolePresenter
from nl2sql.runners.pipeline_runner import PipelineRunner
from nl2sql.common.settings import settings
from nl2sql_cli.types import RunConfig
from nl2sql_cli.common.decorators import handle_cli_errors


@handle_cli_errors
def run_pipeline(
    config: RunConfig, 
    datasource_registry: DatasourceRegistry, 
    llm_registry: LLMRegistry, 
    vector_store: OrchestratorVectorStore
) -> None:
    """Executes the NL2SQL pipeline."""
    if not config.query:
        return
        
    presenter = ConsolePresenter()
    presenter.print_query(config.query)
    
    # Instantiate Runner
    runner = PipelineRunner(datasource_registry, llm_registry, vector_store)
    
    # Setup Monitoring
    from nl2sql.services.callbacks.monitor import PipelineMonitorCallback
    monitor = PipelineMonitorCallback(presenter)
    
    presenter.start_interactive_status("[bold green]Thinking...[/bold green]")
    
    # Execution
    result = runner.run(
        query=config.query,
        role=config.role,
        datasource_id=config.datasource_id,
        execute=not config.no_exec,
        callbacks=[monitor]
    )
    
    presenter.stop_interactive_status()
    
    # Handle Result
    if not result.success:
        if result.traceback:
            presenter.print_error(result.traceback)
        presenter.print_error(result.error or "Unknown Pipeline Error")
        sys.exit(1)
        
    final_state = result.final_state
    
    # ... Presentation Logic ...
    
    # Print Static Status Tree
    #monitor.get_status_tree()

    query_history = final_state.get("query_history", [])
    
    if config.verbose:
        reasoning = final_state.get("reasoning", [])
        presenter.print_execution_tree(config.query, query_history, top_level_reasoning=reasoning)
        
        # Save detailed trace
        try:
            with open("last_reasoning.json", "w") as f:
                dump_data = {
                    "global_reasoning": reasoning,
                    "execution_history": query_history
                }
                json.dump(dump_data, f, indent=2, default=str)
            presenter.console.print("[dim]Detailed reasoning trace saved to last_reasoning.json[/dim]")
        except Exception:
            pass

    if query_history:
        for item in query_history:
            ds = item.get("datasource_id", "Unknown")
            ds_type = item.get("datasource_type", "Unknown")
            sub_query = item.get("sub_query")
            sql = item.get("sql")
            
            if sql:
                header = f"[bold]Datasource: {ds} ({ds_type})[/bold]"
                if sub_query:
                    header = f"[bold]Sub-Query: {sub_query}[/bold]\n" + header
                presenter.print_sql(f"{header}\n\n{sql}")
    
    # Fallback for single execution if history is empty (e.g. direct run)
    elif final_state.get("sql_draft"):
        sql_draft_data = final_state.get("sql_draft")
        sql_draft = sql_draft_data.get("sql") if isinstance(sql_draft_data, dict) else getattr(sql_draft_data, "sql", None)
        if sql_draft:
             presenter.print_sql(f"[bold]SQL Generated:[/bold]\n{sql_draft}")

    # Print Pipeline Errors
    errors = final_state.get("errors")
    if errors:
        presenter.print_pipeline_errors(errors)

    final_answer = final_state.get("final_answer")
    if type(final_answer) == str and final_answer:
        presenter.print_final_answer(final_answer)
    elif type(final_answer) == list and final_answer:
        presenter.print_execution_result(final_answer)
    else:
        return
        
    execution = final_state.get("execution")
    if execution:
        row_count = execution.get("row_count", 0) if isinstance(execution, dict) else getattr(execution, "row_count", 0)
        presenter.print_rows_returned(row_count)


    if config.show_perf:
        tree, metrics, node_map = monitor.get_performance_tree()
        presenter.print_performance_tree(tree, metrics, node_map)
    
    
    from nl2sql.common.metrics import TOKEN_LOG
    presenter.print_cost_summary(result.duration, TOKEN_LOG)
