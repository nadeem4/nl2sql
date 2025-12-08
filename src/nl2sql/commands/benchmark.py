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

def run_benchmark(args: argparse.Namespace, query: str, datasource_registry: DatasourceRegistry, vector_store: SchemaVectorStore) -> None:
    console = Console()
    
    # Mode 1: Dataset Evaluation
    if args.dataset:
        _run_dataset_evaluation(args, datasource_registry, vector_store, console)
        return

    # Mode 2: Config Benchmark (Existing)
    if not args.bench_config:
        console.print("[bold red]Error:[/bold red] --bench-config is required for benchmark mode.", file=sys.stderr)
        sys.exit(1)
        
    if not args.bench_config.exists():
        console.print(f"[bold red]Error:[/bold red] Benchmark config file not found: {args.bench_config}", file=sys.stderr)
        sys.exit(1)

    try:
        bench_data = yaml.safe_load(args.bench_config.read_text()) or {}
    except Exception as e:
        console.print(f"[bold red]Error reading benchmark config:[/bold red] {e}", file=sys.stderr)
        sys.exit(1)
        
    if not isinstance(bench_data, dict):
        console.print("[bold red]Error:[/bold red] Benchmark config must be a dictionary of named configurations.", file=sys.stderr)
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
        console.print(f"[bold red]Error:[/bold red] Dataset file not found: {dataset_path}", file=sys.stderr)
        sys.exit(1)
        
    try:
        dataset = yaml.safe_load(dataset_path.read_text())
    except Exception as e:
        console.print(f"[bold red]Error reading dataset:[/bold red] {e}", file=sys.stderr)
        sys.exit(1)
        
    if not isinstance(dataset, list):
        console.print("[bold red]Error:[/bold red] Dataset must be a list of test cases.", file=sys.stderr)
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
    
    results = []
    
    for item in track(dataset, description="Evaluating..."):
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
            results.append({
                "id": q_id,
                "status": "ERROR",
                "error": str(e),
                "routing_match": False,
                "sql_match": False
            })
            continue
            
        # Check Routing
        actual_ds = state.get("datasource_id")
        routing_info = state.get("routing_info", {})
        routing_layer = routing_info.get("layer", "unknown")
        routing_reasoning = routing_info.get("reasoning", "-")
        routing_tokens = routing_info.get("tokens", "-")
        routing_latency = routing_info.get("latency", 0.0)
        
        routing_latency = routing_info.get("latency", 0.0)
        l1_score = routing_info.get("l1_score", 0.0)
        
        routing_match = (actual_ds == expected_ds)
        
        # Check Layer Match
        layer_match = True
        if expected_layer:
            # Normalize layer names just in case
            layer_match = (routing_layer == expected_layer)
        
        if args.routing_only:
            results.append({
                "id": q_id,
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
                "candidates": routing_info.get("candidates", []),
                "expected_layer": expected_layer,
                "layer_match": layer_match
            })
            continue
            
        # Check SQL/Execution
        generated_sql_data = state.get("sql_draft")
        generated_sql = generated_sql_data.get("sql") if isinstance(generated_sql_data, dict) else getattr(generated_sql_data, "sql", None)
        
        execution_res = state.get("execution")
        generated_rows = execution_res.get("rows") if isinstance(execution_res, dict) else getattr(execution_res, "rows", [])
        exec_error = execution_res.get("error") if isinstance(execution_res, dict) else getattr(execution_res, "error", None)
        
        if exec_error:
            results.append({
                "id": q_id,
                "status": "EXEC_FAIL",
                "error": exec_error,
                "routing_match": routing_match,
                "sql_match": False,
                "gen_sql": generated_sql
            })
            continue
            
        if not generated_sql:
             results.append({
                "id": q_id,
                "status": "NO_SQL",
                "routing_match": routing_match,
                "sql_match": False
            })
             continue

        # 2. Run Expected SQL (Ground Truth)
        # We assume the expected SQL is valid for the EXPECTED datasource.
        # If the router picked the wrong DS, we likely can't run the expected SQL against it easily without error.
        # But we should try running it against the EXPECTED DS to get the ground truth data.
        
        if not expected_ds:
             results.append({
                "id": q_id,
                "status": "BAD_CONFIG",
                "error": "Dataset missing expected datasource",
                "routing_match": routing_match,
                "sql_match": False
            })
             continue
             
        try:
            # Run Expected SQL on Expected DS
            profile = datasource_registry.get_profile(expected_ds)
            from sqlalchemy import create_engine
            engine = create_engine(profile.connection_string)
            with engine.connect() as conn:
                expected_rows_res = conn.execute(text(expected_sql))
                expected_rows = [dict(row._mapping) for row in expected_rows_res]
        except Exception as e:
             results.append({
                "id": q_id,
                "status": "GT_FAIL", # Ground Truth Execution Failed
                "error": str(e),
                "routing_match": routing_match,
                "sql_match": False,
                "gen_sql": generated_sql
            })
             continue
             
        # 3. Compare Results
        try:
            df_gen = pd.DataFrame(generated_rows)
            df_exp = pd.DataFrame(expected_rows)
            
            if not df_gen.empty:
                df_gen = df_gen.sort_values(by=list(df_gen.columns)).reset_index(drop=True)
            if not df_exp.empty:
                df_exp = df_exp.sort_values(by=list(df_exp.columns)).reset_index(drop=True)
            
            match = df_gen.equals(df_exp)
            
            results.append({
                "id": q_id,
                "status": "PASS" if match else "DATA_MISMATCH",
                "routing_match": routing_match,
                "sql_match": match,
                "gen_sql": generated_sql,
                "exp_sql": expected_sql,
                "gen_rows": len(df_gen),
                "exp_rows": len(df_exp),
                "expected_layer": expected_layer,
                "layer_match": layer_match
            })
            
        except Exception as e:
             results.append({
                "id": q_id,
                "status": "COMPARE_FAIL",
                "error": str(e),
                "routing_match": routing_match,
                "sql_match": False,
                "gen_sql": generated_sql
            })

    # Report
    table = Table(title="Evaluation Results", show_header=True, header_style="bold magenta", expand=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status", justify="center")
    table.add_column("Route", justify="center")
    table.add_column("Layer", justify="center")
    
    if not args.routing_only:
        table.add_column("SQL Match", justify="center")
        table.add_column("Rows", justify="right")
    else:
        table.add_column("Got/Exp DS", justify="left")
        
    table.add_column("Reasoning", justify="left", max_width=40, overflow="ellipsis")
    table.add_column("L1 Score", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Latency", justify="right")
    table.add_column("Candidates", justify="left")
    
    correct_routing_count = 0
    correct_sql_count = 0
    valid_sql_count = 0
    
    for r in results:
        status_style = "green" if r["status"] == "PASS" else "red"
        route_icon = "YES" if r["routing_match"] else "NO"
        
        # Format Layer
        layer_raw = r.get("routing_layer", "unknown")
        layer_map = {"layer_1": "L1", "layer_2": "L2", "layer_3": "L3", "fallback": "FB"}
        layer_str = layer_map.get(layer_raw, layer_raw)
        
        # Highlight Layer Mismatch
        exp_layer = r.get("expected_layer")
        if exp_layer and not r["layer_match"]:
             # If mismatch, show: L3(!L1) or similar
             exp_short = layer_map.get(exp_layer, exp_layer)
             layer_str = f"[red]{layer_str}[/red] (exp: {exp_short})"
        
        if r["routing_match"]: correct_routing_count += 1
        
        cols = [
            r["id"],
            f"[{status_style}]{r['status']}[/{status_style}]",
            route_icon,
            layer_str
        ]
        
        if not args.routing_only:
            sql_icon = "YES" if r["sql_match"] else "NO"
            if r["sql_match"]: correct_sql_count += 1
            if r["status"] not in ["EXEC_FAIL", "NO_SQL", "ERROR", "ROUTE_FAIL", "BAD_CONFIG", "GT_FAIL"]: valid_sql_count += 1
            
            rows_info = f"{r.get('gen_rows', '-')} / {r.get('exp_rows', '-')}"
            cols.extend([sql_icon, rows_info])
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
    
    # Calculate Routing Metrics
    total_queries = len(dataset)
    layer_counts = {"layer_1": 0, "layer_2": 0, "layer_3": 0, "fallback": 0}
    
    for r in results:
        layer = r.get("routing_layer", "unknown")
        if layer in layer_counts:
            layer_counts[layer] += 1
            
    routing_acc = (correct_routing_count / len(dataset)) * 100
    console.print(f"\n[bold]Routing Accuracy:[/bold]   {routing_acc:.1f}%")
    
    # Display Routing Breakdown
    console.print("\n[bold]Routing Layer Breakdown:[/bold]")
    for layer, count in layer_counts.items():
        pct = (count / total_queries) * 100
        console.print(f"  - {layer.replace('_', ' ').title()}: {count} ({pct:.1f}%)")

    
    if not args.routing_only:
        sql_acc = (correct_sql_count / len(dataset)) * 100
        valid_sql_rate = (valid_sql_count / len(dataset)) * 100
        console.print(f"\n[bold]Execution Accuracy:[/bold] {sql_acc:.1f}%")
        console.print(f"[bold]Valid SQL Rate:[/bold]     {valid_sql_rate:.1f}%")

