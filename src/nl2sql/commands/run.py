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
from nl2sql.nodes.decomposer import DecomposerNode
from nl2sql.nodes.router import RouterNode
from nl2sql.nodes.router import RouterNode
from nl2sql.nodes.intent import IntentNode
from nl2sql.reporting import ConsolePresenter

def run_pipeline(args: argparse.Namespace, query: Optional[str], datasource_registry: DatasourceRegistry, llm_registry: LLMRegistry, vector_store: SchemaVectorStore) -> None:
    if not query:
        return
        
    if args.node:
        _run_node_mode(args, query, datasource_registry, llm_registry)
    else:
        _run_simple_mode(args, query, datasource_registry, llm_registry, vector_store)


def _run_node_mode(args: argparse.Namespace, query: str, datasource_registry: DatasourceRegistry, llm_registry: LLMRegistry) -> None:
    presenter = ConsolePresenter()
    node_name = args.node.lower()
    presenter.print_step(f"Running Single Node: {node_name}")
    presenter.print_query(query)

    try:
        if node_name == "decomposer":
            llm = llm_registry.decomposer_llm()
            node = DecomposerNode(llm, datasource_registry)
            state = GraphState(user_query=query)
            
            with presenter.console.status("[bold green]Decomposing...[/bold green]", spinner="dots"):
                result = node(state)
            
            presenter.print_node_output("Decomposer", result)
            
        elif node_name == "router":
            node = RouterNode(llm_registry, datasource_registry, args.vector_store)
            state = GraphState(user_query=query)
            
            with presenter.console.status("[bold green]Routing...[/bold green]", spinner="dots"):
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
            presenter.print_node_output("Router", output)

        elif node_name == "intent":
            llm = llm_registry.intent_llm()
            node = IntentNode(llm)
            state = GraphState(user_query=query)
            
            with presenter.console.status("[bold green]Analyzing Intent...[/bold green]", spinner="dots"):
                state = node(state)
            
            # Convert Pydantic model to dict for JSON serialization
            intent_data = state.intent.model_dump() if state.intent else None
            
            output = {
                "intent": intent_data,
                "thoughts": state.thoughts.get("intent", [])
            }
            presenter.print_node_output("Intent", output)
            
        else:
            presenter.print_error(f"Node '{node_name}' execution is not yet supported in isolation.")
            return

    except Exception as e:
        presenter.print_error(f"Error executing node: {e}")


def _run_simple_mode(args: argparse.Namespace, query: str, datasource_registry: DatasourceRegistry, llm_registry: LLMRegistry, vector_store: SchemaVectorStore) -> None:
    presenter = ConsolePresenter()
    presenter.print_query(query)
    
    final_state = {}
    
    with presenter.console.status("[bold green]Thinking...[/bold green]", spinner="dots"):
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
            import traceback
            presenter.print_error(f"{e}")
            presenter.print_error(traceback.format_exc())
            sys.exit(1)

    # Display SQLs from History (for multi-datasource queries)
    query_history = final_state.get("query_history", [])
    if query_history:
        for item in query_history:
            ds = item.get("datasource_id", "Unknown")
            ds_type = item.get("datasource_type", "Unknown")
            sql = item.get("sql")
            if sql:
                presenter.print_sql(f"[bold]Datasource: {ds} ({ds_type})[/bold]\n\n{sql}")
    
    # Fallback for single execution if history is empty (e.g. direct run)
    elif final_state.get("sql_draft"):
        sql_draft_data = final_state.get("sql_draft")
        sql_draft = sql_draft_data.get("sql") if isinstance(sql_draft_data, dict) else getattr(sql_draft_data, "sql", None)
        if sql_draft:
             presenter.print_sql(f"[bold]SQL Generated:[/bold]\n{sql_draft}")

    # Display Final Answer
    final_answer = final_state.get("final_answer")
    if final_answer:
        presenter.print_final_answer(final_answer)
        
    # Display Execution Result Summary
    execution = final_state.get("execution")
    if execution:
        row_count = execution.get("row_count", 0) if isinstance(execution, dict) else getattr(execution, "row_count", 0)
        presenter.print_rows_returned(row_count)

    # Display Used Datasources
    datasource_id = final_state.get("datasource_id")
    if datasource_id:
        presenter.print_datasource_used(str(datasource_id))

    # Visualization
    if args.visualize and "_trace" in final_state:
        draw_execution_trace(
            final_state["_trace"],
            final_state["_graph"],
            final_state["_execution_subgraph"],
            final_state["_planning_subgraph"]
        )

    # Display Performance Metrics
    if args.show_perf:
        presenter.print_performance_report(
            final_state.get("latency", {}),
            llm_registry.get_token_log()
        )
