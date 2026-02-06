import sys
import json
from typing import Optional

from nl2sql.datasources import DatasourceRegistry
from nl2sql.llm import LLMRegistry
from nl2sql.indexing.vector_store import VectorStore
from nl2sql_cli.reporting import ConsolePresenter
from nl2sql.pipeline.pipeline_runner import PipelineRunner
from nl2sql.common.settings import settings
from nl2sql_cli.types import RunConfig
from nl2sql_cli.common.decorators import handle_cli_errors
from nl2sql.context import NL2SQLContext

@handle_cli_errors
def run_pipeline(
    config: RunConfig, 
    ctx: NL2SQLContext
) -> None:
    """Executes the NL2SQL pipeline."""
    if not config.query:
        return
        
    presenter = ConsolePresenter()
    presenter.print_info(f"Query: {config.query}")
    if config.no_exec:
        presenter.print_warning("Execution disabled (no_exec). Only SQL/plan output will be shown.")
    
    # Instantiate Runner
    runner = PipelineRunner(ctx)
    
    # Setup Monitoring
    from nl2sql.services.callbacks.monitor import PipelineMonitorCallback
    monitor = PipelineMonitorCallback(presenter)
    
    presenter.start_interactive_status("Thinking...")
    
    # Execution
    result = runner.run(
        query=config.query,
        role=config.role,
        datasource_id=config.ds_id,
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
    

    result_refs = final_state.get("result_refs", {})
    sub_queries = final_state.get("sub_queries", [])
    sq_map = {sq.id: sq for sq in sub_queries}

    query_history = []
    subgraph_outputs = final_state.get("subgraph_outputs") or {}
    if subgraph_outputs:
        for _subgraph_id, output in subgraph_outputs.items():
            if isinstance(output, dict):
                sub_query = output.get("sub_query")
                sql_draft = output.get("sql_draft")
            else:
                sub_query = getattr(output, "sub_query", None)
                sql_draft = getattr(output, "sql_draft", None)

            if not sub_query:
                continue

            if isinstance(sub_query, dict):
                sq_id = sub_query.get("id")
                ds_id = sub_query.get("datasource_id")
                intent = sub_query.get("intent")
            else:
                sq_id = getattr(sub_query, "id", None)
                ds_id = getattr(sub_query, "datasource_id", None)
                intent = getattr(sub_query, "intent", None)

            entry: Dict[str, Any] = {
                "sub_query": intent,
                "datasource_id": ds_id,
                "sql": sql_draft,
            }

            result_id = result_refs.get(sq_id)
            if result_id:
                frame = ctx.result_store.get(result_id)
                metadata = ctx.result_store.get_metadata(result_id)
                entry["datasource_id"] = metadata.get("datasource_id", ds_id)
                entry["execution"] = {
                    "row_count": frame.row_count,
                    "columns": [c.name for c in frame.columns],
                }

            query_history.append(entry)
    else:
        for sq_id, result_id in result_refs.items():
            sq = sq_map.get(sq_id)
            if sq:
                frame = ctx.result_store.get(result_id)
                metadata = ctx.result_store.get_metadata(result_id)
                query_history.append({
                    "sub_query": sq.intent,
                    "datasource_id": metadata.get("datasource_id", sq.datasource_id),
                    "execution": {
                        "row_count": frame.row_count,
                        "columns": [c.name for c in frame.columns],
                    },
                })
    
    if config.verbose:
        reasoning = final_state.get("reasoning", [])
        presenter.print_execution_tree(config.query, query_history, top_level_reasoning=reasoning)
        
        try:
            with open("last_reasoning.json", "w") as f:
                dump_data = {
                    "global_reasoning": reasoning,
                    "execution_history": query_history
                }
                json.dump(dump_data, f, indent=2, default=str)
            presenter.print_info("Detailed reasoning trace saved to last_reasoning.json")
        except Exception:
            pass

    if query_history:
        datasources_used = sorted({item.get("datasource_id") for item in query_history if item.get("datasource_id")})
        if datasources_used:
            presenter.print_info(f"Datasources used: {', '.join(datasources_used)}")

        for item in query_history:
            ds = item.get("datasource_id", "Unknown")
            sub_query = item.get("sub_query")
            sql = item.get("sql")

            if sql:
                header = f"[bold]Datasource: {ds}[/bold]"
                if sub_query:
                    header = f"[bold]Sub-Query: {sub_query}[/bold]\n" + header
                presenter.print_sql(f"{header}\n\n{sql}")
    
    elif final_state.get("sql_draft"):
        sql_draft_data = final_state.get("sql_draft")
        sql_draft = sql_draft_data.get("sql") if isinstance(sql_draft_data, dict) else getattr(sql_draft_data, "sql", None)
        if sql_draft:
             presenter.print_sql(f"[bold]SQL Generated:[/bold]\n{sql_draft}")

    errors = final_state.get("errors")
    if errors:
        presenter.print_pipeline_errors(errors)

    warnings = final_state.get("warnings") or []
    for warning in warnings:
        if isinstance(warning, dict):
            presenter.print_warning(json.dumps(warning, default=str))
        else:
            presenter.print_warning(str(warning))

    answer_payload = None
    answer_synth = final_state.get("answer_synthesizer_response")
    if answer_synth:
        if isinstance(answer_synth, dict):
            answer_payload = answer_synth.get("final_answer")
        else:
            answer_payload = getattr(answer_synth, "final_answer", None)
    if isinstance(answer_payload, dict) and answer_payload:
        presenter.print_answer_synthesizer_output(answer_payload)

    final_answer = final_state.get("final_answer")
    if isinstance(final_answer, dict) and final_answer:
        presenter.print_answer_synthesizer_output(final_answer)
    elif type(final_answer) == str and final_answer:
        presenter.print_final_answer(final_answer)
    elif type(final_answer) == list and final_answer:
        presenter.print_execution_result(final_answer)
        
    execution = final_state.get("execution")
    if execution:
        row_count = execution.get("row_count", 0) if isinstance(execution, dict) else getattr(execution, "row_count", 0)
        presenter.print_rows_returned(row_count)


    if config.show_perf:
        tree, metrics, node_map = monitor.get_performance_tree()
        presenter.print_performance_tree(tree, metrics, node_map)
    
    
    from nl2sql.common.metrics import TOKEN_LOG
    presenter.print_cost_summary(result.duration, TOKEN_LOG)
