import statistics
import json
import csv
from typing import List, Dict, Any, Optional
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown
from rich.columns import Columns
from rich.console import Group

class ConsolePresenter:
    """
    Handles all TUI presentation and reporting logic for the NL2SQL CLI.
    """
    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()

    # -------------------------------------------------------------------------
    # Generic Helpers
    # -------------------------------------------------------------------------
    def print_error(self, message: str) -> None:
        self.console.print(f"[bold red]Error:[/bold red] {message}")

    def print_warning(self, message: str) -> None:
        self.console.print(f"[yellow]Warning: {message}[/yellow]")

    def print_step(self, message: str) -> None:
        self.console.print(f"[bold blue]{message}[/bold blue]")

    def print_header(self, message: str) -> None:
        self.console.print(f"\n[bold magenta]--- {message} ---[/bold magenta]")

    def print_panel(self, content: Any, title: str, style: str = "green") -> None:
        if isinstance(content, (dict, list)):
            content = json.dumps(content, indent=2, default=str)
        self.console.print(Panel(content, title=title, border_style=style))

    # -------------------------------------------------------------------------
    # Pipeline Execution (run.py)
    # -------------------------------------------------------------------------
    def print_query(self, query: str) -> None:
        self.console.print(f"[bold blue]Query:[/bold blue] {query}")

    def print_node_output(self, node_name: str, output: Any) -> None:
        title = f"{node_name.capitalize()} Output"
        self.print_panel(output, title=title, style="green")

    def print_sql(self, sql: str, title: str = "SQL Generated") -> None:
        self.console.print(Panel(sql, title=title, border_style="cyan", expand=False))

    def print_final_answer(self, answer: str) -> None:
        self.console.print(Panel(Markdown(answer), title="[bold green]Final Answer[/bold green]", expand=False))

    def print_rows_returned(self, count: int) -> None:
         self.console.print(f"[dim]Rows returned: {count}[/dim]")

    def print_datasource_used(self, ds_id: str) -> None:
        self.console.print(f"[bold blue]Datasource Used:[/bold blue] {ds_id}")

    def print_performance_report(self, latency: Dict[str, Any], token_log: List[Dict[str, Any]]) -> None:
        """Renders the detailed performance metrics tables."""
        renderables = []
        
        # 1. Top Level Performance
        top_table = Table(title="Top Level Performance", show_header=True, header_style="bold magenta", expand=True)
        top_table.add_column("Metric", style="dim")
        top_table.add_column("Decomposer", justify="right")
        top_table.add_column("Aggregator", justify="right")
        
        # Identify datasources from latency keys
        datasources = set()
        for key in latency.keys():
            if ":" in key:
                datasources.add(key.split(":")[0])
        sorted_ds = sorted(list(datasources))
        
        for ds in sorted_ds:
            top_table.add_column(f"Exec ({ds})", justify="right")
        top_table.add_column("Total", justify="right", style="bold")
            
        # Latency Row
        lat_decomp = latency.get("decomposer", 0.0)
        lat_agg = latency.get("aggregator", 0.0)
        
        lat_row = ["Latency (s)", f"{lat_decomp:.4f}", f"{lat_agg:.4f}"]
        
        max_branch_latency = 0.0
        for ds in sorted_ds:
            val = latency.get(f'{ds}:total', 0.0)
            lat_row.append(f"{val:.4f}")
            if val > max_branch_latency:
                max_branch_latency = val
                
        total_latency = lat_decomp + lat_agg + max_branch_latency
        lat_row.append(f"{total_latency:.4f}")
        top_table.add_row(*lat_row)
        
        # Token Usage Row
        def sum_tokens(agent_prefix=None, ds_id=None):
            total = 0
            for entry in token_log:
                if agent_prefix and entry["agent"].startswith(agent_prefix):
                    total += entry["total_tokens"]
                elif ds_id and entry.get("datasource_id") == ds_id:
                    # Exclude decomposer/aggregator if they somehow got tagged with ds_id
                    if not (entry["agent"].startswith("decomposer") or entry["agent"].startswith("aggregator")):
                        total += entry["total_tokens"]
            return total

        tok_decomp = sum_tokens(agent_prefix="decomposer")
        tok_agg = sum_tokens(agent_prefix="aggregator")
        
        tok_row = ["Token Usage", str(tok_decomp), str(tok_agg)]
        total_tokens = tok_decomp + tok_agg
        for ds in sorted_ds:
            val = sum_tokens(ds_id=ds)
            tok_row.append(str(val))
            total_tokens += val
        tok_row.append(str(total_tokens))
        top_table.add_row(*tok_row)
        
        renderables.append(top_table)
        renderables.append("\n")

        # 2. Per Datasource Performance
        ai_nodes = {"planner", "intent", "router", "summarizer", "generator", "decomposer", "aggregator"}
        
        ds_metrics = {}
        for key, val in latency.items():
            if ":" in key:
                parts = key.split(":", 1)
                ds_id = parts[0]
                node = parts[1]
                if node == "total": continue
                if ds_id not in ds_metrics:
                    ds_metrics[ds_id] = {}
                ds_metrics[ds_id][node] = val
        
        ds_tables = []
        for ds_id in sorted_ds:
            ds_table = Table(title=f"Performance: {ds_id}", show_header=True, header_style="bold cyan", expand=True)
            ds_table.add_column("Node", style="dim")
            ds_table.add_column("Type", justify="center")
            ds_table.add_column("Model", justify="center")
            ds_table.add_column("Latency (s)", justify="right")
            ds_table.add_column("Tokens", justify="right")
            
            metrics = ds_metrics.get(ds_id, {})
            node_order = ["intent", "planner", "generator", "executor"]
            other_nodes = sorted([n for n in metrics.keys() if n not in node_order])
            sorted_nodes = [n for n in node_order if n in metrics] + other_nodes
            
            for node in sorted_nodes:
                duration = metrics[node]
                is_ai = node in ai_nodes
                node_type = "AI" if is_ai else "Non-AI"
                
                model_name = "-"
                tokens = 0
                if is_ai:
                    for entry in token_log:
                        if entry.get("datasource_id") == ds_id and entry["agent"] == node:
                            model_name = entry["model"]
                            tokens += entry["total_tokens"]
                
                ds_table.add_row(
                    node.capitalize(), 
                    node_type, 
                    model_name, 
                    f"{duration:.4f}", 
                    str(tokens) if is_ai else "-"
                )
            ds_tables.append(ds_table)
            
        if ds_tables:
            renderables.append(Columns(ds_tables))

        if renderables:
            self.print_panel(Group(*renderables), title="Performance & Metrics", style="magenta")

    # -------------------------------------------------------------------------
    # Benchmarking (benchmark.py)
    # -------------------------------------------------------------------------
    def print_config_benchmark_results(self, results: List[Dict[str, Any]]) -> None:
        """Prints the table for LLM Config benchmarking."""
        table = Table(title="Benchmark Results", show_header=True, header_style="bold magenta")
        table.add_column("Config", style="cyan")
        table.add_column("Success Rate", justify="right")
        table.add_column("Avg Latency", justify="right")
        table.add_column("Avg Tokens", justify="right")

        for res in results:
            sr = res['success_rate']
            sr_style = "green" if sr == 100 else "yellow" if sr >= 50 else "red"
            
            table.add_row(
                res['config'],
                f"[{sr_style}]{sr:.1f}%[/{sr_style}]",
                f"{res['avg_latency']:.2f}s",
                f"{res['avg_tokens']:.1f}"
            )

        self.console.print("\n")
        self.console.print(table)

    def print_dataset_benchmark_results(self, results: List[Dict[str, Any]], iterations: int = 1, routing_only: bool = False) -> None:
        """Dispatch to appropriate table display based on iterations."""
        if iterations > 1:
            self._print_pass_k_table(results, iterations)
        else:
            self._print_standard_table(results, routing_only)

    def _print_pass_k_table(self, results: List[Dict[str, Any]], iterations: int) -> None:
        """Prints aggregated results for Pass@K stability testing."""
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
        
        for qid, runs in grouped.items():
            n = len(runs)
            success_count = sum(1 for r in runs if r["status"] == "PASS")
            success_rate = (success_count / n) * 100
            
            ds_counts = {}
            for r in runs:
                ds = r.get("actual_ds", "None")
                ds_counts[ds] = ds_counts.get(ds, 0) + 1
            most_common_ds = max(ds_counts, key=ds_counts.get)
            stability_rate = (ds_counts[most_common_ds] / n) * 100
            
            sql_match_count = sum(1 for r in runs if r.get("sql_match") is True)
            exec_acc = (sql_match_count / n) * 100
            
            sem_match_count = sum(1 for r in runs if r.get("semantic_sql_match") is True)
            sem_acc = (sem_match_count / n) * 100
            
            avg_latency = statistics.mean([r.get("routing_latency", 0) for r in runs])
            
            errors_set = {r["error"] for r in runs if r.get("error")}
            error_str = str(list(errors_set)[0]) if errors_set else "-"
            if len(error_str) > 30: error_str = error_str[:27] + "..."
            
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
          
        self.console.print(table)

    def _print_standard_table(self, results: List[Dict[str, Any]], routing_only: bool) -> None:
        """Prints standard result table for single-pass evaluation."""
        table = Table(title="Evaluation Results", show_header=True, header_style="bold magenta", expand=True)
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Status", justify="center")
        table.add_column("Route", justify="center")
        table.add_column("Layer", justify="center")
        
        if not routing_only:
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
            
            layer_raw = r.get("routing_layer", "unknown")
            layer_map = {"layer_1": "L1", "layer_2": "L2", "layer_3": "L3", "fallback": "FB"}
            layer_str = layer_map.get(layer_raw, layer_raw)
            
            cols = [
                r["id"],
                f"[{status_style}]{r['status']}[/{status_style}]",
                route_icon,
                layer_str
            ]
            
            if not routing_only:
                sql_icon = "YES" if r.get("sql_match") else "NO" if r.get("sql_match") is not None else "-"
                sem_icon = "YES" if r.get("semantic_sql_match") else "NO" if r.get("semantic_sql_match") is not None else "-"
                
                rows_info = f"{r.get('gen_rows', '-')} / {r.get('exp_rows', '-')}"
                cols.extend([sql_icon, sem_icon, rows_info])
            else:
                ds_info = f"{r.get('actual_ds')} / {r.get('expected_ds')}"
                cols.append(ds_info)
                
            reasoning = r.get("routing_reasoning", "-")
            tokens = str(r.get("routing_tokens", "-"))
            latency_val = r.get("routing_latency", 0)
            latency_str = f"{latency_val:.2f}s" if isinstance(latency_val, (int, float)) else "-"
            score_val = r.get("l1_score", 0.0)
            score_str = f"{score_val:.3f}" if isinstance(score_val, (int, float)) else "-"
            
            candidates = r.get("candidates", [])
            cand_str = ""
            if candidates:
                cand_str = ", ".join([f"{c['id']}({c['score']:.2f})" for c in candidates[:3]]) # Show top 3
                if len(candidates) > 3:
                    cand_str += "..."
            
            cols.extend([reasoning, score_str, tokens, latency_str, cand_str])
                
            table.add_row(*cols)
            
        self.console.print(table)

    def print_metrics_summary(self, metrics: Dict[str, Any], results: List[Dict[str, Any]], routing_only: bool = False) -> None:
        """Prints calculated metrics summary."""
        routing_acc = metrics.get("routing_accuracy", 0.0)
        self.console.print(f"\n[bold]Routing Accuracy:[/bold]   {routing_acc:.1f}%")
        
        self.console.print("\n[bold]Routing Layer Breakdown:[/bold]")
        layer_counts = metrics.get("layer_distribution", {})
        layer_pcts = metrics.get("layer_percentages", {})
        
        for layer, count in layer_counts.items():
            pct = layer_pcts.get(layer, 0.0)
            self.console.print(f"  - {layer.replace('_', ' ').title()}: {count} ({pct:.1f}%)")

        if not routing_only:
            sql_acc = metrics.get("execution_accuracy", 0.0)
            sem_acc = metrics.get("semantic_sql_accuracy", 0.0)
            valid_sql_rate = metrics.get("valid_sql_rate", 0.0)
            self.console.print(f"\n[bold]Execution Accuracy:[/bold]    {sql_acc:.1f}%")
            self.console.print(f"[bold]Semantic SQL Accuracy:[/bold] {sem_acc:.1f}%")
            self.console.print(f"[bold]Valid SQL Rate:[/bold]        {valid_sql_rate:.1f}%")

        errors = [r for r in results if r["status"] in ["ERROR", "GT_FAIL", "EXEC_FAIL", "INVALID_GT", "INVALID_SQL"]]
        if errors:
            unique_errors = {}
            for e in errors:
                unique_errors[f"{e['id']}: {e.get('error')}"] = e
            
            self.console.print("\n[bold red]Top Errors:[/bold red]")
            for k in list(unique_errors.keys())[:5]:
                self.console.print(k)

    def export_results(self, results: List[Dict[str, Any]], path: Path) -> None:
        """Exports results to JSON or CSV."""
        export_data = []
        for r in results:
            item = {
                "id": r.get("id"),
                "question": r.get("question", ""), 
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
            
        if path.suffix.lower() == ".json":
            with open(path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2, default=str)
            self.console.print(f"\n[bold green]Results exported to {path}[/bold green]")
            
        elif path.suffix.lower() == ".csv":
            if not export_data:
                self.console.print(f"\n[yellow]No results to export.[/yellow]")
            else:
                keys = export_data[0].keys()
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=keys)
                    writer.writeheader()
                    writer.writerows(export_data)
                self.console.print(f"\n[bold green]Results exported to {path}[/bold green]")
        else:
             self.console.print(f"\n[bold red]Unsupported export format: {path.suffix}. Use .json or .csv[/bold red]")

    # -------------------------------------------------------------------------
    # Indexing (indexing.py)
    # -------------------------------------------------------------------------
    def print_indexing_start(self, path: str) -> None:
         self.console.print(f"[bold blue]Indexing schema to:[/bold blue] {path}")

    def print_indexing_error(self, ds_id: str, error: str) -> None:
        self.console.print(f"[red]Failed to index {ds_id}: {error}[/red]")

    def print_indexing_complete(self) -> None:
        self.console.print("[bold green]Indexing complete![/bold green]")
        
    def create_progress(self):
        """Returns a configured Progress object."""
        from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=self.console
        )

    # -------------------------------------------------------------------------
    # Visualization (visualize.py)
    # -------------------------------------------------------------------------
    def print_graph_saved(self, path: str) -> None:
        import os
        abs_path = os.path.abspath(path)
        self.console.print(f"Graph visualization saved to: [bold underline][link=file:///{abs_path}]{abs_path}[/link][/bold underline]")

    def print_graph_save_error(self, error: str) -> None:
        self.console.print(f"[bold red]Failed to save graph image:[/bold red] {error}")

    def track(self, sequence, description: str = "Working...", total: Optional[float] = None):
        """Wraps rich.progress.track for progress bars."""
        from rich.progress import track
        return track(sequence, description=description, total=total, console=self.console)
