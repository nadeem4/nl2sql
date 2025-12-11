import sys
import argparse
import json
from typing import Optional
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

from nl2sql.langgraph_pipeline import run_with_graph
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.llm_registry import LLMRegistry
from nl2sql.vector_store import SchemaVectorStore
from nl2sql.commands.visualize import draw_execution_trace
from nl2sql.schemas import GraphState
from nl2sql.nodes.decomposer import DecomposerNode
from nl2sql.nodes.router import RouterNode
from nl2sql.nodes.intent import IntentNode

def run_pipeline(args: argparse.Namespace, query: Optional[str], datasource_registry: DatasourceRegistry, llm_registry: LLMRegistry, vector_store: SchemaVectorStore) -> None:
    # Always run in simple console mode
    # Always run in simple console mode
    if not query:
        return
        
    if args.node:
        _run_node_mode(args, query, datasource_registry, llm_registry)
    else:
        _run_simple_mode(args, query, datasource_registry, llm_registry, vector_store)


def _run_node_mode(args: argparse.Namespace, query: str, datasource_registry: DatasourceRegistry, llm_registry: LLMRegistry) -> None:
    console = Console()
    node_name = args.node.lower()
    console.print(f"[bold blue]Running Single Node:[/bold blue] {node_name}")
    console.print(f"[bold blue]Query:[/bold blue] {query}")

    try:
        if node_name == "decomposer":
            llm = llm_registry.decomposer_llm()
            node = DecomposerNode(llm, datasource_registry)
            state = GraphState(user_query=query)
            
            with console.status("[bold green]Decomposing...[/bold green]", spinner="dots"):
                result = node(state)
            
            console.print(Panel(json.dumps(result, indent=2), title="Decomposer Output", border_style="green"))
            
        elif node_name == "router":
            node = RouterNode(llm_registry, datasource_registry, args.vector_store)
            state = GraphState(user_query=query)
            
            with console.status("[bold green]Routing...[/bold green]", spinner="dots"):
                state = node(state)
            
            # Extract nested routing info
            full_routing = state.routing_info
            ds_ids = state.datasource_id
            
            ds_routing = {}
            if isinstance(ds_ids, list):
                # If multiple, show a dict of them
                for ds in ds_ids:
                    info = full_routing.get(ds)
                    ds_routing[ds] = info.model_dump() if hasattr(info, "model_dump") else info
            elif isinstance(ds_ids, str):
                info = full_routing.get(ds_ids)
                ds_routing = info.model_dump() if hasattr(info, "model_dump") else info
            else:
                ds_routing = {k: v.model_dump() if hasattr(v, "model_dump") else v for k, v in full_routing.items()}

            output = {
                "datasource_id": state.datasource_id,
                "routing_info": ds_routing,
                "thoughts": state.thoughts.get("router", [])
            }
            console.print(Panel(json.dumps(output, indent=2, default=str), title="Router Output", border_style="green"))

        elif node_name == "intent":
            llm = llm_registry.intent_llm()
            node = IntentNode(llm)
            state = GraphState(user_query=query)
            
            with console.status("[bold green]Analyzing Intent...[/bold green]", spinner="dots"):
                state = node(state)
            
            # Convert Pydantic model to dict for JSON serialization
            intent_data = state.intent.model_dump() if state.intent else None
            
            output = {
                "intent": intent_data,
                "thoughts": state.thoughts.get("intent", [])
            }
            console.print(Panel(json.dumps(output, indent=2, default=str), title="Intent Output", border_style="green"))
            
        else:
            console.print(f"[bold red]Error:[/bold red] Node '{node_name}' execution is not yet supported in isolation.")
            return

    except Exception as e:
        console.print(f"[bold red]Error executing node:[/bold red] {e}")


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
                visualize=args.visualize,
                show_outputs=args.show_outputs,
                log_requests=args.log_requests,
                on_thought=None # No thoughts in simple mode
            )
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            sys.exit(1)

    # Display SQLs from History (for multi-datasource queries)
    query_history = final_state.get("query_history", [])
    if query_history:
        for item in query_history:
            ds = item.get("datasource_id", "Unknown")
            ds_type = item.get("datasource_type", "Unknown")
            sql = item.get("sql")
            if sql:
                console.print(Panel(f"[bold]Datasource: {ds} ({ds_type})[/bold]\n\n{sql}", title="Generated SQL", border_style="cyan", expand=False))
    
    # Fallback for single execution if history is empty (e.g. direct run)
    elif final_state.get("sql_draft"):
        sql_draft_data = final_state.get("sql_draft")
        sql_draft = sql_draft_data.get("sql") if isinstance(sql_draft_data, dict) else getattr(sql_draft_data, "sql", None)
        if sql_draft:
             console.print(Panel(f"[bold]SQL Generated:[/bold]\n{sql_draft}", title="SQL", border_style="cyan", expand=False))

    # Display Final Answer
    final_answer = final_state.get("final_answer")
    if final_answer:
        console.print(Panel(Markdown(final_answer), title="[bold green]Final Answer[/bold green]", expand=False))
        
    # Display Execution Result Summary
    execution = final_state.get("execution")
    if execution:
        row_count = execution.get("row_count", 0) if isinstance(execution, dict) else getattr(execution, "row_count", 0)
        console.print(f"[dim]Rows returned: {row_count}[/dim]")

    # Display Used Datasources
    datasource_id = final_state.get("datasource_id")
    if datasource_id:
        console.print(f"[bold blue]Datasource Used:[/bold blue] {datasource_id}")

    # Visualization
    if args.visualize and "_trace" in final_state:
        draw_execution_trace(
            final_state["_trace"],
            final_state["_graph"],
            final_state["_execution_subgraph"],
            final_state["_planning_subgraph"]
        )

    # Display Performance Metrics
    # Display Performance Metrics
    if args.show_perf:
        from rich.table import Table
        from rich.columns import Columns
        from rich.console import Group
        
        latency = final_state.get("latency", {})
        renderables = []
        
        # 1. Top Level Performance
        top_table = Table(title="Top Level Performance", show_header=True, header_style="bold magenta", expand=True)
        top_table.add_column("Metric", style="dim")
        top_table.add_column("Decomposer", justify="right")
        top_table.add_column("Aggregator", justify="right")
        
        # Identify datasources from latency keys
        datasources = set()
        for key in latency.keys():
            if ":" in key:
                datasources.add(key.split(":")[0])
        sorted_ds = sorted(list(datasources))
        
        for ds in sorted_ds:
            top_table.add_column(f"Exec ({ds})", justify="right")
        top_table.add_column("Total", justify="right", style="bold")
            
        # Latency Row
        lat_decomp = latency.get("decomposer", 0.0)
        lat_agg = latency.get("aggregator", 0.0)
        
        lat_row = ["Latency (s)", f"{lat_decomp:.4f}", f"{lat_agg:.4f}"]
        
        max_branch_latency = 0.0
        for ds in sorted_ds:
            val = latency.get(f'{ds}:total', 0.0)
            lat_row.append(f"{val:.4f}")
            if val > max_branch_latency:
                max_branch_latency = val
                
        total_latency = lat_decomp + lat_agg + max_branch_latency
        lat_row.append(f"{total_latency:.4f}")
        top_table.add_row(*lat_row)
        
        # Token Usage Row
        token_log = llm_registry.get_token_log()
        
        def sum_tokens(agent_prefix=None, ds_id=None):
            total = 0
            for entry in token_log:
                if agent_prefix and entry["agent"].startswith(agent_prefix):
                    total += entry["total_tokens"]
                elif ds_id and entry.get("datasource_id") == ds_id:
                    # Exclude decomposer/aggregator if they somehow got tagged with ds_id (unlikely but safe)
                    if not (entry["agent"].startswith("decomposer") or entry["agent"].startswith("aggregator")):
                        total += entry["total_tokens"]
            return total

        tok_decomp = sum_tokens(agent_prefix="decomposer")
        tok_agg = sum_tokens(agent_prefix="aggregator")
        
        tok_row = ["Token Usage", str(tok_decomp), str(tok_agg)]
        total_tokens = tok_decomp + tok_agg
        for ds in sorted_ds:
            val = sum_tokens(ds_id=ds)
            tok_row.append(str(val))
            total_tokens += val
        tok_row.append(str(total_tokens))
        top_table.add_row(*tok_row)
        
        renderables.append(top_table)
        renderables.append("\n")

        # 2. Per Datasource Performance
        ai_nodes = {"planner", "intent", "router", "summarizer", "generator", "decomposer", "aggregator"}
        
        # Group latency by datasource
        ds_metrics = {}
        for key, val in latency.items():
            if ":" in key:
                parts = key.split(":", 1)
                ds_id = parts[0]
                node = parts[1]
                if node == "total": continue
                if ds_id not in ds_metrics:
                    ds_metrics[ds_id] = {}
                ds_metrics[ds_id][node] = val
        
        ds_tables = []
        for ds_id in sorted_ds:
            ds_table = Table(title=f"Performance: {ds_id}", show_header=True, header_style="bold cyan", expand=True)
            ds_table.add_column("Node", style="dim")
            ds_table.add_column("Type", justify="center")
            ds_table.add_column("Model", justify="center")
            ds_table.add_column("Latency (s)", justify="right")
            ds_table.add_column("Tokens", justify="right")
            
            metrics = ds_metrics.get(ds_id, {})
            # Sort nodes: intent -> planner -> generator -> executor -> others
            node_order = ["intent", "planner", "generator", "executor"]
            other_nodes = sorted([n for n in metrics.keys() if n not in node_order])
            sorted_nodes = [n for n in node_order if n in metrics] + other_nodes
            
            for node in sorted_nodes:
                duration = metrics[node]
                is_ai = node in ai_nodes
                node_type = "AI" if is_ai else "Non-AI"
                
                # Get Model and Tokens
                model_name = "-"
                tokens = 0
                if is_ai:
                    # Find matching entries in token log
                    for entry in token_log:
                        if entry.get("datasource_id") == ds_id and entry["agent"] == node:
                            model_name = entry["model"]
                            tokens += entry["total_tokens"]
                
                ds_table.add_row(
                    node.capitalize(), 
                    node_type, 
                    model_name, 
                    f"{duration:.4f}", 
                    str(tokens) if is_ai else "-"
                )
            ds_tables.append(ds_table)
            
        if ds_tables:
            renderables.append(Columns(ds_tables))

        if renderables:
            console.print(Panel(Group(*renderables), title="Performance & Metrics", border_style="magenta", expand=True))
