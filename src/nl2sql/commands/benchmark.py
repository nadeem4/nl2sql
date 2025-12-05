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
        target_ds = item.get("datasource")
        
        # 1. Run Pipeline
        try:
            state = run_with_graph(
                registry=datasource_registry,
                llm_registry=llm_registry,
                user_query=question,
                datasource_id=target_ds, # Force routing if specified in dataset
                execute=True,
                vector_store=vector_store,
                vector_store_path=args.vector_store
            )
        except Exception as e:
            results.append({
                "id": q_id,
                "status": "ERROR",
                "error": str(e),
                "match": False
            })
            continue
            
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
                "match": False,
                "gen_sql": generated_sql
            })
            continue
            
        if not generated_sql:
             results.append({
                "id": q_id,
                "status": "NO_SQL",
                "match": False
            })
             continue

        # 2. Run Expected SQL (Ground Truth)
        # We need to execute this against the SAME datasource that was used/requested.
        # If target_ds is set, use it. If not, use the one selected by the pipeline.
        actual_ds_id = state.get("datasource_id")
        
        if not actual_ds_id:
             results.append({
                "id": q_id,
                "status": "NO_DS",
                "match": False,
                "gen_sql": generated_sql
            })
             continue
             
        try:
            profile = datasource_registry.get_profile(actual_ds_id)
            # We need a way to execute raw SQL. The registry doesn't expose a direct execute method easily without an engine.
            # We can create an engine temporarily or use the one from the profile if cached (not currently cached in registry).
            # Let's create an engine.
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
                "match": False,
                "gen_sql": generated_sql
            })
             continue
             
        # 3. Compare Results
        # Convert to pandas for easy comparison
        try:
            df_gen = pd.DataFrame(generated_rows)
            df_exp = pd.DataFrame(expected_rows)
            
            # Normalize: sort by all columns if not empty
            if not df_gen.empty:
                df_gen = df_gen.sort_values(by=list(df_gen.columns)).reset_index(drop=True)
            if not df_exp.empty:
                df_exp = df_exp.sort_values(by=list(df_exp.columns)).reset_index(drop=True)
                
            # Check equality
            # We need to be careful about column names if aliases differ. 
            # Strict comparison: Data and Schema must match.
            # Relaxed comparison: Data values must match (ignoring column names? Risky).
            # Let's do strict for now, assuming generated SQL should match schema.
            
            # Align column types if possible (e.g. all to string) to avoid type mismatch issues
            # Or just use equals()
            
            match = df_gen.equals(df_exp)
            
            results.append({
                "id": q_id,
                "status": "PASS" if match else "DATA_MISMATCH",
                "match": match,
                "gen_sql": generated_sql,
                "exp_sql": expected_sql,
                "gen_rows": len(df_gen),
                "exp_rows": len(df_exp)
            })
            
        except Exception as e:
             results.append({
                "id": q_id,
                "status": "COMPARE_FAIL",
                "error": str(e),
                "match": False,
                "gen_sql": generated_sql
            })

    # Report
    table = Table(title="Evaluation Results", show_header=True, header_style="bold magenta")
    table.add_column("ID", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Match", justify="center")
    table.add_column("Rows (Gen/Exp)", justify="right")
    
    correct_count = 0
    valid_sql_count = 0
    
    for r in results:
        status_style = "green" if r["status"] == "PASS" else "red"
        match_icon = "YES" if r["match"] else "NO"
        
        if r["match"]: correct_count += 1
        if r["status"] not in ["EXEC_FAIL", "NO_SQL", "ERROR"]: valid_sql_count += 1
        
        rows_info = f"{r.get('gen_rows', '-')} / {r.get('exp_rows', '-')}"
        
        table.add_row(
            r["id"],
            f"[{status_style}]{r['status']}[/{status_style}]",
            match_icon,
            rows_info
        )
        
    console.print(table)
    
    accuracy = (correct_count / len(dataset)) * 100
    valid_sql = (valid_sql_count / len(dataset)) * 100
    
    console.print(f"\n[bold]Execution Accuracy:[/bold] {accuracy:.1f}%")
    console.print(f"[bold]Valid SQL Rate:[/bold]     {valid_sql:.1f}%")

