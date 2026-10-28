from langgraph.graph import END, StateGraph

from nl2sql.runtime import Runtime
from nl2sql.pipeline.graph_utils import SQL_AGENT_SUBGRAPH, wrap_subgraph
from nl2sql.pipeline.nodes.aggregator import EngineAggregatorNode
from nl2sql.pipeline.nodes.answer_synthesizer import AnswerSynthesizerNode
from nl2sql.pipeline.nodes.curated_matcher import CuratedMatcherNode, curated_answers_on
from nl2sql.pipeline.nodes.datasource_resolver import DatasourceResolverNode
from nl2sql.pipeline.nodes.decomposer import DecomposerNode
from nl2sql.pipeline.pinned import PinnedEntryNode
from nl2sql.pipeline.routes import aggregator_route, build_scan_layer_router, curated_route, pinned_route, resolver_route
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.subgraphs.sql_agent import build_sql_agent_graph

def build_graph(
    ctx: Runtime,
    execute: bool = True,
    pinned: bool = False,
    answer: bool = True,
) -> StateGraph:
    """Builds the main LangGraph pipeline.

    Constructs the graph with Semantic Analysis, Decomposer, Execution branches,
    and Aggregator.

    When curated answers are configured (``ctx.curated`` holds a verified
    query) and the run executes, the curated matcher is the entry point: a hit
    goes straight to the layer router, which finds the scan done and sends the
    run to the aggregator; anything else continues at the datasource
    resolver. Otherwise the matcher is not in the graph at all, so the graph
    is exactly the one without curated answers.

    ``pinned`` builds the graph of a run whose decomposition is pinned
    (``state.pinned``: a supplied plan, an approval's, or Wave B's chosen
    one). It enters at ``pinned_entry``, which checks the datasources against
    the caller's scope and goes to the layer router; there is no curated
    matcher, resolver or decomposer in it. Any other run's graph has no
    ``pinned_entry`` at all.

    Args:
        ctx (Runtime): The application context containing registries and services.
        execute (bool): Whether to allow execution against real databases.
        pinned (bool): Build the pinned run's graph.
        answer (bool): False builds the graph without the answer synthesizer: the run ends at the
            aggregator and writes no prose (a dashboard refresh; wave_c.md, D119).

    Returns:
        StateGraph: The compiled LangGraph runnable.
    """
    graph = StateGraph(GraphState)

    resolver_node = DatasourceResolverNode(ctx)
    decomposer_node = DecomposerNode(ctx)
    aggregator_node = EngineAggregatorNode(ctx)

    sql_agent_subgraph = build_sql_agent_graph(ctx, execute=execute)

    graph.add_node("datasource_resolver", resolver_node)
    graph.add_node("decomposer", decomposer_node)
    graph.add_node(
        SQL_AGENT_SUBGRAPH,
        wrap_subgraph(sql_agent_subgraph, SQL_AGENT_SUBGRAPH, ctx, execute=execute),
    )
    graph.add_node("aggregator", aggregator_node)
    graph.add_node("layer_router", lambda state: {})

    if pinned:
        graph.add_node("pinned_entry", PinnedEntryNode(ctx))
        graph.set_entry_point("pinned_entry")
        graph.add_conditional_edges("pinned_entry", pinned_route, {"continue": "layer_router", "end": END})
    elif execute and curated_answers_on(ctx):
        graph.add_node("curated_matcher", CuratedMatcherNode(ctx))
        graph.set_entry_point("curated_matcher")
        graph.add_conditional_edges(
            "curated_matcher",
            curated_route,
            {"verified": "layer_router", "generated": "datasource_resolver"},
        )
    else:
        graph.set_entry_point("datasource_resolver")

    graph.add_conditional_edges(
        "datasource_resolver",
        resolver_route,
        {"continue": "decomposer", "end": END},
    )

    # The decomposer builds the execution DAG itself, so its output is all the
    # router needs: there is no node between them.
    route_scan_layers = build_scan_layer_router(ctx, execute=execute)

    graph.add_edge("decomposer", "layer_router")
    graph.add_conditional_edges(
        "layer_router",
        route_scan_layers,
        [SQL_AGENT_SUBGRAPH, "aggregator", END],
    )

    graph.add_edge(SQL_AGENT_SUBGRAPH, "layer_router")
    if answer:
        graph.add_node("answer_synthesizer", AnswerSynthesizerNode(ctx))
        graph.add_conditional_edges(
            "aggregator",
            aggregator_route,
            {"answer_synthesizer": "answer_synthesizer", END: END},
        )
        graph.add_edge("answer_synthesizer", END)
    else:
        graph.add_edge("aggregator", END)

    return graph.compile()
