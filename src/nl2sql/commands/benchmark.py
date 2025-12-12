import sys
import argparse
import statistics
import yaml
from rich.console import Console
from rich.table import Table
from rich.progress import track

from nl2sql.llm_registry import parse_llm_config, LLMRegistry, reset_usage, get_usage_summary
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.vector_store import SchemaVectorStore
from nl2sql.langgraph_pipeline import run_with_graph
from nl2sql.evaluation.evaluator import ModelEvaluator

def run_benchmark(args: argparse.Namespace, query: str, datasource_registry: DatasourceRegistry, vector_store: SchemaVectorStore) -> None:
    console = Console()
    
    # Mode 1: Dataset Evaluation
    if args.dataset:
        _run_dataset_evaluation(args, datasource_registry, vector_store, console)
        return

    # Mode 2: Config Benchmark (Existing)
    if not args.bench_config:
        console.print("[bold red]Error:[/bold red] --bench-config is required for benchmark mode.")
        sys.exit(1)
        
    if not args.bench_config.exists():
        console.print(f"[bold red]Error:[/bold red] Benchmark config file not found: {args.bench_config}")
        sys.exit(1)

    try:
        bench_data = yaml.safe_load(args.bench_config.read_text()) or {}
    except Exception as e:
        console.print(f"[bold red]Error reading benchmark config:[/bold red] {e}")
        sys.exit(1)
        
    if not isinstance(bench_data, dict):
        console.print("[bold red]Error:[/bold red] Benchmark config must be a dictionary of named configurations.")
        sys.exit(1)

    console.print(f"[bold blue]Starting benchmark for query:[/bold blue] '{query}'")
    results = []
    
    for name, cfg_data in bench_data.items():
        console.print(f"\n[bold magenta]--- Benchmarking Config: {name} ---[/bold magenta]")
        try:
            # Validate it looks like a config
            if not isinstance(cfg_data, dict):
                console.print(f"[yellow]Skipping {name}: invalid format (expected dict)[/yellow]")
                continue
                
            llm_cfg = parse_llm_config(cfg_data)
        except Exception as e:
            console.print(f"[red]Failed to parse config {name}: {e}[/red]")
            continue

        # Benchmark uses the same datasource registry but different LLM configs
        bench_llm_registry = LLMRegistry(llm_cfg)

        latencies = []
        success_count = 0
        total_tokens = 0
        
        # Use rich.progress.track for iteration progress
        for i in track(range(args.iterations), description=f"Running {args.iterations} iterations..."):
            reset_usage()
            
            try:
                state = run_with_graph(
                    registry=datasource_registry,
                    llm_registry=bench_llm_registry,
                    user_query=query,
                    execute=True, 
                    vector_store=vector_store,
                    vector_store_path=args.vector_store
                )
                
                latency = state.get("latency", {}).get("total", 0)
                latencies.append(latency)
                
                # Check success: SQL generated and no execution errors
                if state.get("sql_draft") and not state.get("execution", {}).get("error") and not state.get("errors"):
                    success_count += 1
                else:
                    # Log failure details if needed, but keep it clean for progress bar
                    pass
                
                usage = get_usage_summary()
                total_tokens += usage.get("_all", {}).get("total_tokens", 0)
                
            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")
        
        avg_latency = statistics.mean(latencies) if latencies else 0
        avg_tokens = total_tokens / args.iterations if args.iterations > 0 else 0
        success_rate = (success_count / args.iterations) * 100
        
        results.append({
            "config": name,
            "avg_latency": avg_latency,
            "success_rate": success_rate,
            "avg_tokens": avg_tokens
        })

    # Create Result Table
    table = Table(title="Benchmark Results", show_header=True, header_style="bold magenta")
    table.add_column("Config", style="cyan")
    table.add_column("Success Rate", justify="right")
    table.add_column("Avg Latency", justify="right")
    table.add_column("Avg Tokens", justify="right")

    for res in results:
        # Colorize Success Rate
        sr = res['success_rate']
        sr_style = "green" if sr == 100 else "yellow" if sr >= 50 else "red"
        
        table.add_row(
            res['config'],
            f"[{sr_style}]{sr:.1f}%[/{sr_style}]",
            f"{res['avg_latency']:.2f}s",
            f"{res['avg_tokens']:.1f}"
        )

    console.print("\n")
    console.print(table)

def _run_dataset_evaluation(args: argparse.Namespace, datasource_registry: DatasourceRegistry, vector_store: SchemaVectorStore, console: Console) -> None:
    """
    Runs evaluation against a golden dataset.
    """
    import pandas as pd
    from sqlalchemy import text
    
    dataset_path = args.dataset
    if not dataset_path.exists():
        console.print(f"[bold red]Error:[/bold red] Dataset file not found: {dataset_path}")
        sys.exit(1)
        
    try:
        dataset = yaml.safe_load(dataset_path.read_text())
    except Exception as e:
        console.print(f"[bold red]Error reading dataset:[/bold red] {e}")
        sys.exit(1)
        
    if not isinstance(dataset, list):
        console.print("[bold red]Error:[/bold red] Dataset must be a list of test cases.")
        sys.exit(1)
        
    if args.include_ids:
        dataset = [item for item in dataset if item.get("id") in args.include_ids]
        if not dataset:
            console.print(f"[bold red]Error:[/bold red] No test cases found matching IDs: {args.include_ids}")
            sys.exit(1)
            
    console.print(f"[bold blue]Starting evaluation on {len(dataset)} test cases...[/bold blue]")
    
    # Load default LLM config if not provided
    llm_cfg = parse_llm_config({"default": {"provider": "openai", "model": "gpt-4o"}}) # Fallback
    if args.llm_config and args.llm_config.exists():
         # We need to load the full config, but LLMRegistry expects parsed config.
         # For now, let's assume standard loading.
         from nl2sql.llm_registry import load_llm_config
         llm_cfg = load_llm_config(args.llm_config)

    # Override for stub LLM if requested
    if getattr(args, "stub_llm", False):
        llm_cfg.default.provider = "stub"
        for agent_cfg in llm_cfg.agents.values():
            agent_cfg.provider = "stub"

    llm_registry = LLMRegistry(llm_cfg)
    
    llm_registry = LLMRegistry(llm_cfg)
    
    # ---------------------------------------------------------
    # Helper: Evaluate Single Case
    # ---------------------------------------------------------
    def _evaluate_case(item: dict) -> dict:
        q_id = item.get("id", "unknown")
        question = item.get("question")
        expected_sql = item.get("expected_sql")
        expected_ds = item.get("datasource")
        expected_layer = item.get("expected_routing_layer")
        
        # 1. Run Pipeline (Auto-Routing)
        try:
            state = run_with_graph(
                registry=datasource_registry,
                llm_registry=llm_registry,
                user_query=question,
                datasource_id=None, # Use the Router!
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
            
        # Validate Routing
        actual_ds = state.get("datasource_id")
        if isinstance(actual_ds, list): 
            actual_ds = actual_ds[0] if actual_ds else None
            
        routing_match = (actual_ds == expected_ds)
        
        # Extract metadata
        all_routing_info = state.get("routing_info", {})
        routing_info = all_routing_info.get(actual_ds)
        
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
            
        # Check SQL/Execution
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

        # 2. Run Expected SQL (Ground Truth)
        if not expected_sql:
             return {
                "id": q_id,
                "question": question,
                "status": "NO_GT", # No Ground Truth
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
            # Run Expected SQL on Expected DS
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
                "status": "GT_FAIL", # Ground Truth Execution Failed
                "error": str(e),
                "routing_match": routing_match,
                "sql_match": False,
                "gen_sql": generated_sql
            }
             
        # 3. Compare Results
        try:
            data_match = ModelEvaluator.compare_results(generated_rows, expected_rows, order_matters=False)
            semantic_sql_match = ModelEvaluator.compare_sql_semantic(generated_sql, expected_sql)

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

    # ---------------------------------------------------------
    # Parallel Execution
    # ---------------------------------------------------------
    # ---------------------------------------------------------
    # Parallel Execution with Iterations
    # ---------------------------------------------------------
    results = []
    
    import concurrent.futures
    # Default to 5 workers for now
    workers = 5 
    iterations = args.iterations if args.iterations else 1
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        # Submit all tasks (dataset * iterations)
        futures = []
        for i in range(iterations):
            for item in dataset:
                futures.append(executor.submit(_evaluate_case, item)) 
        
        total_tasks = len(dataset) * iterations
        for future in track(concurrent.futures.as_completed(futures), total=total_tasks, description=f"Evaluating ({workers} parallel, {iterations} runs)..."):
            results.append(future.result())

    # Sort results by ID for consistent display
    results.sort(key=lambda x: x["id"])
    
    # ---------------------------------------------------------
    # Aggregation & Reporting
    # ---------------------------------------------------------
    if iterations > 1:
        # Group by ID
        grouped = {}
        for r in results:
            qid = r["id"]
            if qid not in grouped: grouped[qid] = []
            grouped[qid].append(r)
            
        table = Table(title=f"Evaluation Results (Pass@{iterations})", show_header=True, header_style="bold magenta", expand=True)
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Success Rate", justify="right")
        table.add_column("Route Stab.", justify="right")
        table.add_column("Exec Acc.", justify="right")
        table.add_column("Sem Acc.", justify="right")
        table.add_column("Avg Latency", justify="right")
        table.add_column("Errors", justify="left", overflow="ellipsis")
        
        aggregate_results = [] # For metrics calculation later
        
        for qid, runs in grouped.items():
            n = len(runs)
            success_count = sum(1 for r in runs if r["status"] == "PASS")
            success_rate = (success_count / n) * 100
            
            # Routing Stability: Most common DS
            ds_counts = {}
            for r in runs:
                ds = r.get("actual_ds", "None")
                ds_counts[ds] = ds_counts.get(ds, 0) + 1
            most_common_ds = max(ds_counts, key=ds_counts.get)
            stability_rate = (ds_counts[most_common_ds] / n) * 100
            
            # Execution Accuracy (SQL Match)
            sql_match_count = sum(1 for r in runs if r.get("sql_match") is True)
            exec_acc = (sql_match_count / n) * 100
            
            # Semantic Accuracy
            sem_match_count = sum(1 for r in runs if r.get("semantic_sql_match") is True)
            sem_acc = (sem_match_count / n) * 100
            
            avg_latency = statistics.mean([r.get("routing_latency", 0) for r in runs])
            
            # Top error if any
            errors_set = {r["error"] for r in runs if r.get("error")}
            error_str = str(list(errors_set)[0]) if errors_set else "-"
            if len(error_str) > 30: error_str = error_str[:27] + "..."
            
            # Style
            sr_style = "green" if success_rate == 100 else "yellow" if success_rate >= 50 else "red"
            
            table.add_row(
                qid,
                f"[{sr_style}]{success_rate:.0f}%[/{sr_style}]",
                f"{stability_rate:.0f}%",
                f"{exec_acc:.0f}%",
                f"{sem_acc:.0f}%",
                f"{avg_latency:.2f}s",
                error_str
            )
            
            # Use the "best" result or last result for the main metrics aggregation?
            # Or define aggregate metrics differently?
            # Existing metrics assume list of dicts.
            # Let's pass ALL results to metric calculator? No, it expects unique IDs usually.
            # Actually, `calculate_aggregate_metrics` takes a list of results. If we pass flattened list, it calculates global averages.
            # So `aggregate_results` = `results` is fine for global stats.
            
        console.print(table)
        
    else:
        # Logic for Single Run (Original Table)
        table = Table(title="Evaluation Results", show_header=True, header_style="bold magenta", expand=True)
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Status", justify="center")
        table.add_column("Route", justify="center")
        table.add_column("Layer", justify="center")
        
        if not args.routing_only:
            table.add_column("SQL Match", justify="center")
            table.add_column("Sem Match", justify="center")
            table.add_column("Rows", justify="right")
        else:
            table.add_column("Got/Exp DS", justify="left")
            
        table.add_column("Reasoning", justify="left", max_width=40, overflow="ellipsis")
        table.add_column("L1 Score", justify="right")
        table.add_column("Tokens", justify="right")
        table.add_column("Latency", justify="right")
        table.add_column("Candidates", justify="left")
        
        for r in results:
            status_style = "green" if r["status"] == "PASS" else "red"
            route_icon = "YES" if r["routing_match"] else "NO"
            
            # Format Layer
            layer_raw = r.get("routing_layer", "unknown")
            layer_map = {"layer_1": "L1", "layer_2": "L2", "layer_3": "L3", "fallback": "FB"}
            layer_str = layer_map.get(layer_raw, layer_raw)
            
            cols = [
                r["id"],
                f"[{status_style}]{r['status']}[/{status_style}]",
                route_icon,
                layer_str
            ]
            
            if not args.routing_only:
                sql_icon = "YES" if r.get("sql_match") else "NO" if r.get("sql_match") is not None else "-"
                sem_icon = "YES" if r.get("semantic_sql_match") else "NO" if r.get("semantic_sql_match") is not None else "-"
                
                rows_info = f"{r.get('gen_rows', '-')} / {r.get('exp_rows', '-')}"
                cols.extend([sql_icon, sem_icon, rows_info])
            else:
                ds_info = f"{r.get('actual_ds')} / {r.get('expected_ds')}"
                cols.append(ds_info)
                
            # Add Reasoning and Tokens and Latency
            reasoning = r.get("routing_reasoning", "-")
            tokens = str(r.get("routing_tokens", "-"))
            latency_val = r.get("routing_latency", 0)
            latency_str = f"{latency_val:.2f}s" if isinstance(latency_val, (int, float)) else "-"
            score_val = r.get("l1_score", 0.0)
            score_str = f"{score_val:.3f}" if isinstance(score_val, (int, float)) else "-"
            
            # Format Candidates
            candidates = r.get("candidates", [])
            cand_str = ""
            if candidates:
                cand_str = ", ".join([f"{c['id']}({c['score']:.2f})" for c in candidates[:3]]) # Show top 3
                if len(candidates) > 3:
                    cand_str += "..."
            
            cols.extend([reasoning, score_str, tokens, latency_str, cand_str])
                
            table.add_row(*cols)
            
        console.print(table)
    
    # Calculate Routing Metrics via Evaluator (Global)
    # Note: metrics will be aggregated over (dataset_size * iterations) samples
    metrics = ModelEvaluator.calculate_aggregate_metrics(results, len(results))
    
    routing_acc = metrics.get("routing_accuracy", 0.0)
    console.print(f"\n[bold]Routing Accuracy:[/bold]   {routing_acc:.1f}%")
    
    # Display Routing Breakdown
    console.print("\n[bold]Routing Layer Breakdown:[/bold]")
    layer_counts = metrics.get("layer_distribution", {})
    layer_pcts = metrics.get("layer_percentages", {})
    
    for layer, count in layer_counts.items():
        pct = layer_pcts.get(layer, 0.0)
        console.print(f"  - {layer.replace('_', ' ').title()}: {count} ({pct:.1f}%)")

    
    if not args.routing_only:
        sql_acc = metrics.get("execution_accuracy", 0.0)
        sem_acc = metrics.get("semantic_sql_accuracy", 0.0)
        valid_sql_rate = metrics.get("valid_sql_rate", 0.0)
        console.print(f"\n[bold]Execution Accuracy:[/bold]    {sql_acc:.1f}%")
        console.print(f"[bold]Semantic SQL Accuracy:[/bold] {sem_acc:.1f}%")
        console.print(f"[bold]Valid SQL Rate:[/bold]        {valid_sql_rate:.1f}%")

    errors = [r for r in results if r["status"] in ["ERROR", "GT_FAIL", "EXEC_FAIL"]]
    if errors:
        # Unique errors by message to avoid spam in iterations
        unique_errors = {}
        for e in errors:
            unique_errors[f"{e['id']}: {e.get('error')}"] = e
        
        console.print("\n[bold red]Top Errors:[/bold red]")
        for k in list(unique_errors.keys())[:5]:
            console.print(k)

    # Export Results
    if args.export_path:
        import json
        import csv
        
        export_data = []
        for r in results:
            # Re-construct a cleaner dict for export
            item = {
                "id": r.get("id"),
                "question": r.get("question", ""), # Ensure question is captured
                "status": r.get("status"),
                "generated_sql": r.get("gen_sql"),
                "expected_sql": r.get("exp_sql"),
                "sql_match": r.get("sql_match"),
                "semantic_match": r.get("semantic_sql_match"),
                "routing_match": r.get("routing_match"),
                "datasource": r.get("actual_ds"),
                "expected_datasource": r.get("expected_ds"),
                "error": r.get("error")
            }
            export_data.append(item)
            
        path = args.export_path
        if path.suffix.lower() == ".json":
            with open(path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2, default=str)
            console.print(f"\n[bold green]Results exported to {path}[/bold green]")
            
        elif path.suffix.lower() == ".csv":
            if not export_data:
                console.print(f"\n[yellow]No results to export.[/yellow]")
            else:
                keys = export_data[0].keys()
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=keys)
                    writer.writeheader()
                    writer.writerows(export_data)
                console.print(f"\n[bold green]Results exported to {path}[/bold green]")
        else:
             console.print(f"\n[bold red]Unsupported export format: {path.suffix}. Use .json or .csv[/bold red]")

