import sys
import argparse
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

from nl2sql.langgraph_pipeline import run_with_graph
from nl2sql.tui import NL2SQLTUI
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.llm_registry import LLMRegistry
from nl2sql.vector_store import SchemaVectorStore

def run_pipeline(args: argparse.Namespace, query: str, datasource_registry: DatasourceRegistry, llm_registry: LLMRegistry, vector_store: SchemaVectorStore) -> None:
    # If --show-thoughts is set, use the full TUI
    if args.show_thoughts:
        _run_tui_mode(args, query, datasource_registry, llm_registry, vector_store)
    else:
        _run_simple_mode(args, query, datasource_registry, llm_registry, vector_store)


def _run_simple_mode(args: argparse.Namespace, query: str, datasource_registry: DatasourceRegistry, llm_registry: LLMRegistry, vector_store: SchemaVectorStore) -> None:
    console = Console()
    console.print(f"[bold blue]Query:[/bold blue] {query}")
    
    final_state = {}
    
    with console.status("[bold green]Thinking...[/bold green]", spinner="dots"):
        try:
            final_state = run_with_graph(
                registry=datasource_registry,
                llm_registry=llm_registry,
                user_query=query,
                datasource_id=args.id,
                execute=not args.no_exec, 
                vector_store=vector_store,
                vector_store_path=args.vector_store,
                debug=args.debug,
                on_thought=None # No thoughts in simple mode
            )
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            sys.exit(1)

    # Display Final Answer
    final_answer = final_state.get("final_answer")
    if final_answer:
        console.print(Panel(Markdown(final_answer), title="[bold green]Final Answer[/bold green]", expand=False))
    
    # Display SQL if available (optional, but good for context)
    sql_draft_data = final_state.get("sql_draft")
    if sql_draft_data:
        sql_draft = sql_draft_data.get("sql")
        if sql_draft:
            console.print(Panel(f"[bold]SQL Generated:[/bold]\n{sql_draft}", title="SQL", border_style="cyan", expand=False))
        
    # Display Execution Result Summary
    execution = final_state.get("execution")
    if execution:
        row_count = execution.get("row_count", 0)
        console.print(f"[dim]Rows returned: {row_count}[/dim]")


def _run_tui_mode(args: argparse.Namespace, query: str, datasource_registry: DatasourceRegistry, llm_registry: LLMRegistry, vector_store: SchemaVectorStore) -> None:
    # Initialize TUI
    tui = NL2SQLTUI()
    tui.start()
    
    try:
        tui.log_orchestrator(f"Query: {query}")
        tui.log_orchestrator("Initializing graph...")

        # Define TUI callback
        current_node = None
        def tui_callback(node: str, logs: list[str], token: bool = False):
            nonlocal current_node
            
            # Parse node name to determine target panel
            # Format: "NODE_NAME (BRANCH_LABEL)" or just "NODE_NAME"
            if "(" in node and ")" in node:
                parts = node.split(" (")
                base_node = parts[0]
                branch_label = parts[1].strip(")")
                
                # Log to specific branch
                for log in logs:
                    tui.log_branch(branch_label, f"[{base_node}] {log}", title=branch_label)
            else:
                # Log to orchestrator (Decomposer, Aggregator, Router, etc.)
                for log in logs:
                    tui.log_orchestrator(f"[{node}] {log}")

        state = run_with_graph(
            registry=datasource_registry,
            llm_registry=llm_registry,
            user_query=query,
            datasource_id=args.id,
            execute=not args.no_exec, 
            vector_store=vector_store,
            vector_store_path=args.vector_store,
            debug=args.debug,
            on_thought=tui_callback # Always enable callback for TUI
        )
        
        tui.log_orchestrator("Graph execution complete.")
        
        # Display Final Result in TUI (Footer or Orchestrator)
        final_answer = state.get("final_answer")
        if final_answer:
            tui.log_orchestrator("\n[bold green]=== Final Answer ===[/bold green]")
            tui.log_orchestrator(final_answer)
        
        # Keep TUI open until user input (Interactive Mode)
        tui.interact()
        tui.stop()
        
        # Print final result to stdout as well
        if final_answer:
             print("\n=== Final Answer ===")
             print(final_answer)
             
    except Exception as e:
        tui.stop()
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        # Ensure TUI is stopped if not already
        try:
            tui.stop()
        except:
            pass
