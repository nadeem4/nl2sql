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
