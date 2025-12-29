import sys
import argparse
import statistics
import yaml
from nl2sql.core.llm_registry import parse_llm_config, LLMRegistry, get_usage_summary
from nl2sql.core.metrics import reset_usage
from nl2sql.core.datasource_registry import DatasourceRegistry
from nl2sql.core.vector_store import OrchestratorVectorStore
from nl2sql.core.graph import run_with_graph
from nl2sql.evaluation.evaluator import ModelEvaluator
from nl2sql.reporting import ConsolePresenter

def run_benchmark(args: argparse.Namespace, datasource_registry: DatasourceRegistry, vector_store: OrchestratorVectorStore) -> None:
    presenter = ConsolePresenter()
    
    # Matrix Benchmarking
    llm_configs = {}

    if args.bench_config and args.bench_config.exists():
        try:
            bench_data = yaml.safe_load(args.bench_config.read_text()) or {}
            for name, cfg_data in bench_data.items():
                if isinstance(cfg_data, dict):
                     llm_configs[name] = parse_llm_config(cfg_data)
        except Exception as e:
            presenter.print_error(f"Error reading bench config: {e}")
            sys.exit(1)
    
    if not llm_configs:
        llm_cfg = parse_llm_config({"default": {"provider": "openai", "model": "gpt-4o"}}) # Fallback
        if args.llm_config and args.llm_config.exists():
            from nl2sql.core.llm_registry import load_llm_config
            llm_cfg = load_llm_config(args.llm_config)
            
        if getattr(args, "stub_llm", False):
            llm_cfg.default.provider = "stub"
            for agent_cfg in llm_cfg.agents.values():
                agent_cfg.provider = "stub"
                
        llm_configs["default"] = llm_cfg

    # Run Matrix
    for name, llm_cfg in llm_configs.items():
        llm_registry = LLMRegistry(llm_cfg)
        _run_dataset_evaluation(
            args, 
            datasource_registry, 
            vector_store, 
            llm_registry, 
            config_name=name
        )



def _run_dataset_evaluation(
    args: argparse.Namespace, 
    datasource_registry: DatasourceRegistry, 
    vector_store: OrchestratorVectorStore,
    llm_registry: LLMRegistry,
    config_name: str = "default"
) -> None:
    """
    Runs evaluation against a golden dataset.
    """
    import pandas as pd
    from sqlalchemy import text
    
    presenter = ConsolePresenter()
        
    dataset_path = args.dataset
    if not dataset_path.exists():
        presenter.print_error(f"Dataset file not found: {dataset_path}")
        sys.exit(1)
        
    try:
        dataset = yaml.safe_load(dataset_path.read_text())
    except Exception as e:
        presenter.print_error(f"Error reading dataset: {e}")
        sys.exit(1)
        
    if not isinstance(dataset, list):
        presenter.print_error("Dataset must be a list of test cases.")
        sys.exit(1)
        
    if args.include_ids:
        dataset = [item for item in dataset if item.get("id") in args.include_ids]
        if not dataset:
            presenter.print_error(f"No test cases found matching IDs: {args.include_ids}")
            sys.exit(1)
            
    presenter.print_header(f"Evaluating Config: {config_name}")
    presenter.print_step(f"Starting evaluation on {len(dataset)} test cases...")
    

    def _evaluate_case(item: dict) -> dict:
        q_id = item.get("id", "unknown")
        question = item.get("question")
        expected_sql = item.get("expected_sql")
        expected_ds = item.get("datasource")
        expected_layer = item.get("expected_routing_layer")
        
        try:
            state = run_with_graph(
                registry=datasource_registry,
                llm_registry=llm_registry,
                user_query=question,
                datasource_id=None, 
                execute=not args.routing_only,
                vector_store=vector_store,
                vector_store_path=args.vector_store
            )
        except Exception as e:
            return {
                "id": q_id,
                "question": question,
                "status": "ERROR",
                "error": str(e),
                "routing_match": False,
                "sql_match": False
            }
            
        actual_ds = state.get("datasource_id") or set()
        
        expected_set = set(expected_ds) if expected_ds else set()
        
        routing_match = (actual_ds == expected_set)
        
        all_routing_info = state.get("routing_info", {})
        
        combined_routing_info = []
        if actual_ds:
            for ds_id in actual_ds:
                if ds_id in all_routing_info:
                    combined_routing_info.append(all_routing_info[ds_id])
        
        # Use the first one for "Layer" stats, or define a rule (e.g. max layer)
        # For strict compatibility, we can keep primary_id logic for the *single* returned routing_info 
        # but ideally we'd update metrics to be multi-aware.
        
        # For now, let's just pick the "best" one or sum them up?
        # Actually, if we have multiple, we might want to know if *any* used fallback?
        
        # Let's keep the primary_id logic for now but add a comment, 
        # AND if the user wants *full* info, we should probably update how validation works.
        # But to answer "why", I will change it to attempt to find the "worst" or "most significant" one?
        # Or just return the primary one as before but acknowledge it?
        
        # User asked "Why". I should just fix it to be representative.
        
        primary_id = None
        if actual_ds:
             # Sort to be deterministic
            sorted_ds = sorted(list(actual_ds))
            primary_id = sorted_ds[0]
            
        routing_info = all_routing_info.get(primary_id) if primary_id else None
        
        def get_val(obj, key, default=None):
            if isinstance(obj, dict): return obj.get(key, default)
            return getattr(obj, key, default)

        if routing_info:
            routing_layer = get_val(routing_info, "layer", "unknown")
            routing_reasoning = get_val(routing_info, "reasoning", "")
            routing_tokens = get_val(routing_info, "tokens", 0)
            routing_latency = get_val(routing_info, "latency", 0)
            l1_score = get_val(routing_info, "l1_score", 0.0)
            candidates = get_val(routing_info, "candidates", [])
            if candidates and not isinstance(candidates[0], dict):
                candidates = [{"id": c.id, "score": c.score} for c in candidates]
        else:
            routing_layer = "unknown"
            routing_reasoning = "No routing info"
            routing_tokens = 0
            routing_latency = 0
            l1_score = 0.0
            candidates = []

        layer_match = (routing_layer == expected_layer)
        
        if args.routing_only:
            return {
                "id": q_id,
                "question": question,
                "status": "PASS" if routing_match else "ROUTE_FAIL",
                "routing_match": routing_match,
                "sql_match": None,
                "actual_ds": actual_ds,
                "expected_ds": expected_ds,
                "routing_layer": routing_layer,
                "routing_reasoning": routing_reasoning,
                "routing_tokens": routing_tokens,
                "routing_latency": routing_latency,
                "l1_score": l1_score,
                "candidates": candidates,
                "expected_layer": expected_layer,
                "layer_match": layer_match
            }
            
        generated_sql_data = state.get("sql_draft")
        if isinstance(generated_sql_data, str):
            generated_sql = generated_sql_data
        else:
            generated_sql = generated_sql_data.get("sql") if isinstance(generated_sql_data, dict) else getattr(generated_sql_data, "sql", None)
        
        execution_res = state.get("execution")
        generated_rows = execution_res.get("rows") if isinstance(execution_res, dict) else getattr(execution_res, "rows", [])
        exec_error = execution_res.get("error") if isinstance(execution_res, dict) else getattr(execution_res, "error", None)
        
        if exec_error:
            return {
                "id": q_id,
                "question": question,
                "status": "EXEC_FAIL",
                "error": exec_error,
                "routing_match": routing_match,
                "sql_match": False,
                "gen_sql": generated_sql
            }
            
        if not generated_sql:
             return {
                "id": q_id,
                "question": question,
                "status": "NO_SQL",
                "routing_match": routing_match,
                "sql_match": False
            }

        if not expected_sql:
             return {
                "id": q_id,
                "question": question,
                "status": "NO_GT", 
                "routing_match": routing_match,
                "sql_match": None,
                "semantic_sql_match": None,
                "gen_sql": generated_sql
            }

        if not expected_ds:
             return {
                "id": q_id,
                "status": "BAD_CONFIG",
                "error": "Dataset missing expected datasource",
                "routing_match": routing_match,
                "sql_match": False
            }
             
        try:
            profile = datasource_registry.get_profile(expected_ds)
            from sqlalchemy import create_engine
            engine = create_engine(profile.sqlalchemy_url)
            with engine.connect() as conn:
                expected_rows_res = conn.execute(text(expected_sql))
                expected_rows = [dict(row._mapping) for row in expected_rows_res]
        except Exception as e:
             return {
                "id": q_id,
                "question": question,
                "status": "GT_FAIL", 
                "error": str(e),
                "routing_match": routing_match,
                "sql_match": False,
                "gen_sql": generated_sql
            }
             

        try:
            data_match = ModelEvaluator.compare_results(generated_rows, expected_rows, order_matters=False)
            
            try:
                semantic_sql_match = ModelEvaluator.compare_sql_semantic(generated_sql, expected_sql)
            except ValueError as ve:
                err_msg = str(ve)
                if "Ground Truth" in err_msg:
                    return {
                        "id": q_id,
                        "question": question,
                        "status": "INVALID_GT",
                        "error": err_msg,
                        "routing_match": routing_match,
                        "sql_match": None if data_match else False,
                        "semantic_sql_match": None,
                        "gen_sql": generated_sql
                    }
                else: 
                     return {
                        "id": q_id,
                        "question": question,
                        "status": "INVALID_SQL", 
                        "error": err_msg,
                        "routing_match": routing_match,
                        "sql_match": data_match,
                        "semantic_sql_match": False,
                        "gen_sql": generated_sql
                    }

            return {
                "id": q_id,
                "question": question,
                "status": "PASS" if data_match else "DATA_MISMATCH",
                "routing_match": routing_match,
                "sql_match": data_match,
                "semantic_sql_match": semantic_sql_match,
                "gen_sql": generated_sql,
                "exp_sql": expected_sql,
                "gen_rows": len(generated_rows),
                "exp_rows": len(expected_rows),
                "expected_layer": expected_layer,
                "layer_match": layer_match
            }
            
        except Exception as e:
             return {
                "id": q_id,
                "status": "COMPARE_FAIL",
                "error": str(e),
                "routing_match": routing_match,
                "sql_match": False,
                "gen_sql": generated_sql
            }


    results = []
    
    import concurrent.futures
    workers = 5 
    iterations = args.iterations if args.iterations else 1
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = []
        for i in range(iterations):
            for item in dataset:
                futures.append(executor.submit(_evaluate_case, item)) 
        
        total_tasks = len(dataset) * iterations
        for future in presenter.track(concurrent.futures.as_completed(futures), total=total_tasks, description=f"Evaluating ({workers} parallel, {iterations} runs)..."):
            results.append(future.result())

    # Sort results by ID for consistent display
    results.sort(key=lambda x: x["id"])
    
    # Delegate Reporting
    presenter.print_dataset_benchmark_results(results, iterations=iterations, routing_only=args.routing_only)
    
    # Calculate Metrics and Print Summary
    metrics = ModelEvaluator.calculate_aggregate_metrics(results, len(results))
    presenter.print_metrics_summary(metrics, results, routing_only=args.routing_only)

    if args.export_path:
        presenter.export_results(results, args.export_path)

