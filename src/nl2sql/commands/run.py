import sys
import argparse
import json
from typing import Optional

from nl2sql.langgraph_pipeline import run_with_graph
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.llm_registry import LLMRegistry
from nl2sql.vector_store import SchemaVectorStore
from nl2sql.commands.visualize import draw_execution_trace
from nl2sql.schemas import GraphState
from nl2sql.reporting import ConsolePresenter

def run_pipeline(args: argparse.Namespace, query: Optional[str], datasource_registry: DatasourceRegistry, llm_registry: LLMRegistry, vector_store: SchemaVectorStore) -> None:
    if not query:
        return
        
    _run_simple_mode(args, query, datasource_registry, llm_registry, vector_store)


def _run_simple_mode(args: argparse.Namespace, query: str, datasource_registry: DatasourceRegistry, llm_registry: LLMRegistry, vector_store: SchemaVectorStore) -> None:
    presenter = ConsolePresenter()
    presenter.print_query(query)
    
    final_state = {}
    
    import time
    from nl2sql.callbacks.status import StatusCallback
    
    status_callback = StatusCallback(presenter)
    presenter.start_interactive_status("[bold green]Thinking...[/bold green]")
    
    start_time = time.perf_counter()
    try:
        final_state = run_with_graph(
            registry=datasource_registry,
            llm_registry=llm_registry,
            user_query=query,
            datasource_id=args.id,
            execute=not args.no_exec, 
            vector_store=vector_store,
            vector_store_path=args.vector_store,
            callbacks=[status_callback]
        )
    except Exception as e:
        import traceback
        presenter.stop_interactive_status()
        presenter.print_error(f"{e}")
        presenter.print_error(traceback.format_exc())
        sys.exit(1)
    finally:
        presenter.stop_interactive_status()
    
    duration = time.perf_counter() - start_time



    query_history = final_state.get("query_history", [])
    
    if args.verbose:
        reasoning = final_state.get("reasoning", {})
        presenter.print_execution_tree(query, query_history, top_level_reasoning=reasoning)
        
        # Save detailed trace
        try:
            with open("last_reasoning.json", "w") as f:
                json.dump(query_history, f, indent=2, default=str)
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

    final_answer = final_state.get("final_answer")
    if final_answer:
        presenter.print_final_answer(final_answer)
        
    execution = final_state.get("execution")
    if execution:
        row_count = execution.get("row_count", 0) if isinstance(execution, dict) else getattr(execution, "row_count", 0)
        presenter.print_rows_returned(row_count)


    if args.show_perf:
        from nl2sql.metrics import LATENCY_LOG, TOKEN_LOG
        presenter.print_performance_report(
            LATENCY_LOG,
            TOKEN_LOG
        )
    
    from nl2sql.metrics import TOKEN_LOG
    presenter.print_cost_summary(duration, TOKEN_LOG)
