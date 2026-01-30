from langgraph.graph import END, StateGraph

from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.graph_utils import wrap_subgraph
from nl2sql.pipeline.nodes.aggregator import EngineAggregatorNode
from nl2sql.pipeline.nodes.answer_synthesizer import AnswerSynthesizerNode
from nl2sql.pipeline.nodes.datasource_resolver import DatasourceResolverNode
from nl2sql.pipeline.nodes.decomposer import DecomposerNode
from nl2sql.pipeline.nodes.global_planner import GlobalPlannerNode
from nl2sql.pipeline.routes import build_scan_layer_router, resolver_route
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.subgraphs import build_subgraph_registry

def build_graph(
    ctx: NL2SQLContext,
    execute: bool = True,
) -> StateGraph:
    """Builds the main LangGraph pipeline.

    Constructs the graph with Semantic Analysis, Decomposer, Execution branches,
    and Aggregator.

    Args:
        ctx (NL2SQLContext): The application context containing registries and services.
        execute (bool): Whether to allow execution against real databases.

    Returns:
        StateGraph: The compiled LangGraph runnable.
    """
    graph = StateGraph(GraphState)

    resolver_node = DatasourceResolverNode(ctx)
    decomposer_node = DecomposerNode(ctx)
    aggregator_node = EngineAggregatorNode(ctx)
    synthesizer_node = AnswerSynthesizerNode(ctx)
    global_planner_node = GlobalPlannerNode(ctx)

    subgraph_specs = build_subgraph_registry(ctx)
    subgraph_runnables = {
        name: spec.builder(ctx) for name, spec in subgraph_specs.items()
    }

    graph.add_node("datasource_resolver", resolver_node)
    graph.add_node("decomposer", decomposer_node)
    graph.add_node("global_planner", global_planner_node)
    for name, subgraph in subgraph_runnables.items():
        graph.add_node(name, wrap_subgraph(subgraph, name, ctx))
    graph.add_node("aggregator", aggregator_node)
    graph.add_node("answer_synthesizer", synthesizer_node)
    graph.add_node("layer_router", lambda state: {})

    graph.set_entry_point("datasource_resolver")

    graph.add_conditional_edges(
        "datasource_resolver",
        resolver_route,
        {"continue": "decomposer", "end": END},
    )

    graph.add_edge("decomposer", "global_planner")
    route_scan_layers = build_scan_layer_router(ctx, subgraph_specs)

    graph.add_edge("global_planner", "layer_router")
    graph.add_conditional_edges(
        "layer_router",
        route_scan_layers,
        list(subgraph_runnables.keys()) + ["aggregator", END],
    )

    for name in subgraph_runnables.keys():
        graph.add_edge(name, "layer_router")
    graph.add_edge("aggregator", "answer_synthesizer")
    graph.add_edge("answer_synthesizer", END)

    return graph.compile()
