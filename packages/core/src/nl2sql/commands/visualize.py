from typing import List, Dict, Any
from langgraph.graph.state import CompiledStateGraph
from nl2sql.reporting import ConsolePresenter

def draw_execution_trace(trace: List[Dict[str, Any]], graph: CompiledStateGraph, execution_subgraph: CompiledStateGraph, agentic_execution_loop: CompiledStateGraph):
    """
    Visualizes the execution trace in the CLI and saves the graph structure.
    """
    presenter = ConsolePresenter()

    try:
        png_bytes = graph.get_graph(xray=True).draw_mermaid_png()
        import os
        output_path = "graph_trace.png"
        abs_path = os.path.abspath(output_path)
        with open(output_path, "wb") as f:
            f.write(png_bytes)

        presenter.print_graph_saved(abs_path)
    except Exception as e:
        presenter.print_graph_save_error(str(e))
