from typing import List, Dict, Any
from rich.tree import Tree
from rich.console import Console
from rich.panel import Panel
from langgraph.graph.state import CompiledStateGraph

def draw_execution_trace(trace: List[Dict[str, Any]], graph: CompiledStateGraph, execution_subgraph: CompiledStateGraph, planning_subgraph: CompiledStateGraph):
    """
    Visualizes the execution trace in the CLI and saves the graph structure.
    """
    console = Console()
    

    try:
        png_bytes = graph.get_graph(xray=True).draw_mermaid_png()
        import os
        output_path = "graph_trace.png"
        abs_path = os.path.abspath(output_path)
        with open(output_path, "wb") as f:
            f.write(png_bytes)

        console.print(f"Graph visualization saved to: [bold underline][link=file:///{abs_path}]{abs_path}[/link][/bold underline]")
    except Exception as e:
        console.print(f"[bold red]Failed to save graph image:[/bold red] {e}")

