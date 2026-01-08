import sys
import json
from typing import Optional

from nl2sql.pipeline.graph import run_with_graph
from nl2sql.datasources import DatasourceRegistry
from nl2sql.services.llm import LLMRegistry
from nl2sql.services.vector_store import OrchestratorVectorStore
# Updated Import
from nl2sql_cli.commands.visualize import draw_execution_trace
from nl2sql.pipeline.state import GraphState
from nl2sql.reporting import ConsolePresenter
from nl2sql.common.settings import settings
from nl2sql_cli.types import RunConfig


def run_pipeline(
    config: RunConfig, 
    datasource_registry: DatasourceRegistry, 
    llm_registry: LLMRegistry, 
    vector_store: OrchestratorVectorStore
) -> None:
    """Executes the NL2SQL pipeline.

    Args:
        config (RunConfig): Execution configuration.
        datasource_registry (DatasourceRegistry): Registry of datasources.
        llm_registry (LLMRegistry): Registry of LLMs.
        vector_store (OrchestratorVectorStore): Vector store instance.
    """
    if not config.query:
        return
        
    _run_simple_mode(config, datasource_registry, llm_registry, vector_store)


def _run_simple_mode(
    config: RunConfig, 
    datasource_registry: DatasourceRegistry, 
    llm_registry: LLMRegistry, 
    vector_store: OrchestratorVectorStore
) -> None:
    """Invokes the pipeline in simple (non-benchmark) mode.

    Args:
        config (RunConfig): Execution configuration.
        datasource_registry (DatasourceRegistry): Registry of datasources.
        llm_registry (LLMRegistry): Registry of LLMs.
        vector_store (OrchestratorVectorStore): Vector store instance.
    """
    presenter = ConsolePresenter()
    presenter.print_query(config.query)
    
    final_state = {}
    
    import time
    from nl2sql.services.callbacks.monitor import PipelineMonitorCallback
    
    monitor = PipelineMonitorCallback(presenter)
    presenter.start_interactive_status("[bold green]Thinking...[/bold green]")
    
    start_time = time.perf_counter()
    
    # Load Role Context (RBAC)
    policy_context = {}
    try:
        import pathlib
        from nl2sql.security.policies import PolicyConfig
        from pydantic import ValidationError
        
        policies_path = pathlib.Path(settings.policies_config_path)            
        if policies_path.exists():
            with open(policies_path, "r") as f:
                raw_json = f.read()
                
            try:
                # 1. Strict Schema Validation
                policy_cfg = PolicyConfig.model_validate_json(raw_json)
                
                # 2. Look up by Role ID
                role_policy = policy_cfg.get_role(config.role)
                
                if not role_policy:
                    presenter.print_error(f"Critical: Role '{config.role}' not defined in '{policies_path.resolve()}'. Available roles: {list(policy_cfg.root.keys())}")
                    sys.exit(1)
                    
                # 3. Convert to Dict for Pipeline
                policy_context = role_policy.model_dump()
                
            except ValidationError as ve:
                presenter.print_error(f"Policy Configuration Error in '{policies_path}':\n{ve}")
                sys.exit(1)
        else:
            presenter.print_error(f"Critical: Policy config file not found at '{policies_path}'. Cannot load context.")
            sys.exit(1)

    except Exception as e:
        presenter.print_error(f"Failed to load policy context: {e}")
        sys.exit(1)

    try:
        print(f"Policy Context: {policy_context}")
        final_state = run_with_graph(
            registry=datasource_registry,
            llm_registry=llm_registry,
            user_query=config.query,
            datasource_id=config.datasource_id,
            execute=not config.no_exec, 
            vector_store=vector_store,
            vector_store_path=config.vector_store_path,
            callbacks=[monitor],
            user_context=policy_context
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
    presenter.print_cost_summary(duration, TOKEN_LOG)
