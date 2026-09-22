import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from rich.live import Live
from rich.spinner import Spinner
from rich.style import Style
from rich.text import Text


class ConsolePresenter:
    """
    Console presentation utilities for the CLI.
    """

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()
        self._status = None

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------
    def status_context(self, message: str, spinner: str = "dots"):
        return self.console.status(message, spinner=spinner)

    def start_interactive_status(self, message: str, spinner: str = "dots") -> None:
        if self._status:
            self._status.stop()
        self._status = self.console.status(message, spinner=spinner)
        self._status.start()

    def update_interactive_status(self, message: str) -> None:
        if self._status:
            self._status.update(message)
        else:
            self.start_interactive_status(message)

    def stop_interactive_status(self) -> None:
        if self._status:
            self._status.stop()
            self._status = None

    def start_task_line(self, message: str, spinner: str = "dots") -> Live:
        live = Live(Spinner(spinner, text=message), console=self.console, refresh_per_second=8, transient=False)
        live.start()
        return live

    def finish_task_line(self, live: Live, message: str, success: bool = True) -> None:
        label = "✓" if success else "✗"
        style = "green" if success else "red"
        live.update(Text(f"{label} {message}", style=style))
        live.stop()

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------
    def _print_labeled(self, label: str, style: str, message: str) -> None:
        self.console.print(f"[{style}]{escape(label)}[/{style}] {escape(str(message))}")

    def print_success(self, message: str) -> None:
        self._print_labeled("✓", "green", message)

    def print_error(self, message: str) -> None:
        self._print_labeled("[ERROR]", "red", message)

    def print_warning(self, message: str) -> None:
        self._print_labeled("[WARN]", "yellow", message)

    def print_info(self, message: str) -> None:
        self._print_labeled("[INFO]", "blue", message)

    def print_header(self, message: str) -> None:
        self.console.print(f"\n[bold magenta]--- {escape(str(message))} ---[/bold magenta]")

    def print_panel(self, content: Any, title: str, style: str = "green") -> None:
        if isinstance(content, (dict, list)):
            content = json.dumps(content, indent=2, default=str)
        if isinstance(content, str):
            content = Text(content)
        self.console.print(Panel(content, title=escape(str(title)), border_style=style))

    def print_table(
        self,
        data: Union[Sequence[Dict[str, Any]], Sequence[Sequence[Any]]],
        title: str = "",
        columns: Optional[Sequence[str]] = None,
    ) -> None:
        if not data:
            self.console.print("[dim]No data to display.[/dim]")
            return

        table = Table(title=title, show_header=True, header_style="bold cyan", expand=True)

        first = data[0]
        if isinstance(first, dict):
            keys = list(columns) if columns else list(first.keys())
            for key in keys:
                table.add_column(str(key))
            for row in data:
                table.add_row(*[Text(str(row.get(k, ""))) for k in keys])
        else:
            if columns:
                for col in columns:
                    table.add_column(str(col))
            else:
                for idx in range(len(first)):
                    table.add_column(f"Col {idx + 1}")
            for row in data:
                table.add_row(*[Text(str(val)) for val in row])

        self.console.print(table)

    def print_tree(self, tree: Tree, title: Optional[str] = None, style: str = "cyan") -> None:
        if title:
            self.console.print(Panel(tree, title=escape(str(title)), border_style=style))
        else:
            self.console.print(tree)

    # ------------------------------------------------------------------
    # Pipeline execution (run.py)
    # ------------------------------------------------------------------
    def print_pipeline_errors(self, errors: List[Any]) -> None:
        if not errors:
            return

        table = Table(title="Pipeline Errors", show_header=True, header_style="bold red", expand=True)
        table.add_column("Node", style="cyan")
        table.add_column("Severity", justify="center")
        table.add_column("Code", justify="center")
        table.add_column("Message", style="white")

        for e in errors:
            if isinstance(e, dict):
                severity = e.get("severity", "ERROR")
                node = e.get("node", "unknown")
                code = e.get("error_code", "-")
                msg = e.get("message", "-")
            else:
                severity = e.severity.name if hasattr(e.severity, "name") else str(e.severity)
                node = e.node
                code = e.error_code
                msg = e.message

            sev_style = "red"
            if "WARNING" in severity:
                sev_style = "yellow"
            if "CRITICAL" in severity:
                sev_style = "bold red"

            table.add_row(
                Text(str(node).upper()),
                f"[{sev_style}]{escape(str(severity))}[/{sev_style}]",
                Text(str(code)),
                Text(str(msg)),
            )

        self.console.print("\n")
        self.console.print(table)
        self.console.print("\n")

    def print_query(self, query: str) -> None:
        self.console.print(f"[bold blue]Query:[/bold blue] {escape(str(query))}")

    def print_node_output(self, node_name: str, output: Any) -> None:
        title = f"{node_name.capitalize()} Output"
        self.print_panel(output, title=title, style="green")

    def print_sql(self, sql: Any, title: str = "SQL Generated") -> None:
        """Render SQL in a panel.

        A plain string is shown verbatim: SQL is not markup, and T-SQL
        bracket-quoted identifiers such as ``[dbo].[orders]`` would otherwise be
        parsed as style tags and vanish from the output. Callers that want
        styling pass a pre-built renderable (e.g. ``Text``).
        """
        body = Text(sql) if isinstance(sql, str) else sql
        self.console.print(Panel(body, title=escape(str(title)), border_style="cyan", expand=False))

    def print_final_answer(self, answer: str) -> None:
        self.console.print(Panel(Markdown(answer), title="[bold green]Final Answer[/bold green]", expand=False))

    def print_answer_synthesizer_output(self, answer: Dict[str, Any]) -> None:
        summary = answer.get("summary")
        format_type = answer.get("format_type")
        content = answer.get("content")
        warnings = answer.get("warnings") or []

        if summary:
            self.print_panel(Markdown(summary), title="Answer Summary", style="green")

        if content:
            title = "Answer Content"
            if format_type:
                title = f"Answer Content ({format_type})"
            self.print_panel(Markdown(content), title=title, style="cyan")

        if warnings:
            for warning in warnings:
                self.print_warning(str(warning))

    def print_rows_returned(self, count: int) -> None:
        self.console.print(f"[dim]Rows returned: {count}[/dim]")

    def print_execution_result(self, execution: Any) -> None:
        if not execution:
            return

        if isinstance(execution, list):
            rows = execution
            columns = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
        else:
            rows = execution.get("rows", []) if isinstance(execution, dict) else getattr(execution, "rows", [])
            columns = execution.get("columns", []) if isinstance(execution, dict) else getattr(execution, "columns", [])

        if not rows:
            self.console.print("[dim]No rows returned.[/dim]")
            return

        table = Table(title="Result Data", show_header=True, header_style="bold cyan", border_style="blue")
        for col in columns:
            table.add_column(Text(str(col)))

        for row in rows:
            row_vals = []
            for col in columns:
                val = row.get(col, "") if isinstance(row, dict) else getattr(row, col, "")
                row_vals.append(str(val))
            table.add_row(*[Text(v) for v in row_vals])

        self.console.print("\n")
        self.console.print(table)
        self.console.print("\n")

    def print_datasource_used(self, ds_id: str) -> None:
        self.console.print(f"[bold blue]Datasource Used:[/bold blue] {escape(str(ds_id))}")

    def print_execution_tree(
        self,
        user_query: str,
        query_history: List[Dict[str, Any]],
        top_level_reasoning: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        if top_level_reasoning is None:
            top_level_reasoning = []
        tree = Tree(f"[bold blue]Root Query: {escape(str(user_query))}[/bold blue]")

        node_styles = {
            "decomposer": "bold magenta",
            "router": "bold cyan",
            "intent": "bold magenta",
            "schema": "bold yellow",
            "planner": "bold blue",
            "validator": "bold red",
            "summarizer": "bold orange1",
            "generator": "bold green",
            "executor": "bold white",
            "aggregator": "bold magenta",
        }

        def add_reasoning_steps(parent_tree: Tree, reasoning_list: List[Dict[str, Any]]) -> None:
            if not isinstance(reasoning_list, list):
                return

            for step in reasoning_list:
                node = step.get("node", "unknown")
                content = step.get("content")
                msg_type = step.get("type", "info")

                style = node_styles.get(node, "bold")

                node_label = f"[{style}]{node.capitalize()}[/{style}]"
                if msg_type == "error":
                    node_label += " [bold red](Error)[/bold red]"

                step_branch = parent_tree.add(node_label)

                if isinstance(content, list):
                    for line in content:
                        step_branch.add(Text(str(line)))
                else:
                    step_branch.add(Text(str(content)))

        if top_level_reasoning:
            decomp_steps = [r for r in top_level_reasoning if r.get("node") == "decomposer"]
            if decomp_steps:
                add_reasoning_steps(tree, decomp_steps)

        for i, item in enumerate(query_history):
            sub_query = item.get("sub_query") or "Main Branch"
            ds_id = item.get("datasource_id") or "Unknown"
            reasoning = item.get("reasoning", [])

            branch = tree.add(
                f"[bold green]Branch {i+1}:[/bold green] {escape(str(sub_query))} "
                f"[dim]({escape(str(ds_id))})[/dim]"
            )

            add_reasoning_steps(branch, reasoning)

            sql = item.get("sql_draft")
            if not sql:
                sql = item.get("sql")

            if sql:
                branch.add(f"[bold]SQL:[/bold] {escape(str(sql))}")

        if top_level_reasoning:
            agg_steps = [r for r in top_level_reasoning if r.get("node") == "aggregator"]
            if agg_steps:
                add_reasoning_steps(tree, agg_steps)

        self.console.print("\n")
        self.console.print(tree)

    def print_status_tree(
        self,
        tree_data: Dict[str, List[str]],
        durations: Dict[str, float],
        node_order: Optional[List[str]] = None,
    ) -> None:
        if not tree_data:
            return

        root_tree = Tree("Graph")

        def build_branch(parent_name: str, tree_node: Tree):
            children = tree_data.get(parent_name, [])
            for child in children:
                duration = durations.get(child, 0.0)
                label = f"{child} [dim]({duration:.2f}s)[/dim]"
                branch = tree_node.add(label)
                build_branch(child, branch)

        all_children = set()
        for kids in tree_data.values():
            all_children.update(kids)

        all_nodes = set(durations.keys())
        roots = list(all_nodes - all_children)

        if node_order:
            roots.sort(key=lambda x: node_order.index(x) if x in node_order else 9999)
        else:
            roots.sort()

        for r in roots:
            duration = durations.get(r, 0.0)
            label = f"{r} [dim]({duration:.2f}s)[/dim]"
            branch = root_tree.add(label)
            build_branch(r, branch)

        self.console.print("\n")
        self.console.print(root_tree)

    def print_performance_tree(
        self,
        tree_data: Dict[str, List[str]],
        metrics_data: Dict[str, Any],
        node_map: Optional[Dict[str, str]] = None,
        tokens_by_node: Optional[Dict[str, int]] = None,
    ) -> None:
        """``tokens_by_node`` is ``QueryResult.usage`` total tokens keyed by node name."""
        if not tree_data:
            return
        if node_map is None:
            node_map = {}
        tokens_by_node = tokens_by_node or {}

        root_tree = Tree("[bold magenta]Performance Execution Tree[/bold magenta]")

        def get_dur_style(dur):
            if dur > 5.0:
                return "bold red"
            if dur > 2.0:
                return "yellow"
            return "green"

        def build_branch(parent_id: str, tree_node: Tree, path: set):
            for child_id in tree_data.get(parent_id, []):
                if child_id in path:
                    name = node_map.get(child_id, child_id)
                    tree_node.add(f"[dim]{name} (recursive)[/dim]")
                    continue

                meta = metrics_data.get(child_id)
                name = node_map.get(child_id, child_id)

                label = f"[bold]{name}[/bold]"

                if meta:
                    dur = meta.duration
                    dur_style = get_dur_style(dur)
                    label += f" [dim]in[/dim] [{dur_style}]{dur:.2f}s[/{dur_style}]"

                    if tokens_by_node.get(name):
                        label += f" | [cyan]{tokens_by_node[name]} tok[/cyan]"

                    if meta.error:
                        label += f" [bold red]FAILED: {escape(str(meta.error))}[/bold red]"

                branch = tree_node.add(label)

                new_path = set(path)
                new_path.add(child_id)
                build_branch(child_id, branch, new_path)

        all_children = set()
        for parent, kids in tree_data.items():
            for k in kids:
                if k != parent:
                    all_children.add(k)

        all_nodes = set(tree_data.keys())
        roots = [n for n in all_nodes if n not in all_children]

        roots.sort(
            key=lambda r: metrics_data[r].duration if r in metrics_data else 0,
            reverse=True,
        )

        for r_id in roots:
            meta = metrics_data.get(r_id)
            name = node_map.get(r_id, r_id)

            label = f"[bold]{name}[/bold]"
            if meta:
                dur = meta.duration
                dur_style = get_dur_style(dur)
                label += f" [dim]in[/dim] [{dur_style}]{dur:.2f}s[/{dur_style}]"
                if tokens_by_node.get(name):
                    label += f" | [cyan]{tokens_by_node[name]} tok[/cyan]"

            branch = root_tree.add(label)
            build_branch(r_id, branch, {r_id})

        self.console.print("\n")
        self.console.print(Panel(root_tree, title="Trace Metrics", border_style="magenta"))
        self.console.print("\n")

    def print_usage_summary(self, total_duration: float, usage: Dict[str, Any]) -> None:
        """One line totalling the question's LLM usage, from ``QueryResult.usage``."""
        total = (usage or {}).get("total") or {}
        line = (
            f"LLM usage: {total.get('calls', 0)} calls | "
            f"tokens in {total.get('input_tokens', 0)} (cached {total.get('cached_input_tokens', 0)}) "
            f"out {total.get('output_tokens', 0)} (reasoning {total.get('reasoning_tokens', 0)}) | "
            f"{total_duration:.2f}s"
        )
        if total.get("cost") is not None:
            line += f" | cost {total['cost']:.4f}"
        self.console.print(Text(line, style="dim"), soft_wrap=True)

    # ------------------------------------------------------------------
    # Benchmarking (benchmark.py)
    # ------------------------------------------------------------------
    def print_config_benchmark_results(self, results: List[Dict[str, Any]]) -> None:
        table = Table(title="Benchmark Results", show_header=True, header_style="bold magenta")
        table.add_column("Config", style="cyan")
        table.add_column("Success Rate", justify="right")
        table.add_column("Avg Latency", justify="right")
        table.add_column("Avg Tokens", justify="right")

        for res in results:
            sr = res["success_rate"]
            sr_style = "green" if sr == 100 else "yellow" if sr >= 50 else "red"

            table.add_row(
                res["config"],
                f"[{sr_style}]{sr:.1f}%[/{sr_style}]",
                f"{res['avg_latency']:.2f}s",
                f"{res['avg_tokens']:.1f}",
            )

        self.console.print("\n")
        self.console.print(table)

    def print_benchmark_results(self, results: List[Dict[str, Any]], title: str = "Evaluation Results") -> None:
        """One row per (question, role) run: expected outcome, status and why."""
        styles = {"pass": "green", "fail": "red", "skip": "yellow", "xfail": "magenta"}
        table = Table(title=title, show_header=True, header_style="bold magenta", expand=True)
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Role", no_wrap=True)
        table.add_column("Expected", no_wrap=True)
        table.add_column("Status", justify="center", no_wrap=True)
        table.add_column("Rows", justify="right", no_wrap=True)
        table.add_column("Reason", justify="left", overflow="fold")

        for r in results:
            style = styles.get(r["status"], "white")
            rows = "-" if r.get("rows") is None else f"{r['rows']}/{r.get('gold_rows', '-')}"
            table.add_row(r["id"], r["role"], r["expected"], f"[{style}]{r['status'].upper()}[/{style}]",
                          rows, Text(str(r.get("reason") or "")))

        self.console.print(table)

    def print_benchmark_summary(self, metrics: Dict[str, Any]) -> None:
        """Pass/fail/skip/xfail counts per role, then the total."""
        table = Table(title="Summary", show_header=True, header_style="bold magenta")
        table.add_column("Role", style="cyan")
        for outcome in ("pass", "fail", "skip", "xfail"):
            table.add_column(outcome.upper(), justify="right")
        rows = sorted(metrics.get("by_role", {}).items()) + [("total", metrics.get("total", {}))]
        for role, counts in rows:
            table.add_row(role, *(str(counts.get(o, 0)) for o in ("pass", "fail", "skip", "xfail")))
        self.console.print(table)

    def print_tier2_scoreboard(self, board: Dict[str, Any]) -> None:
        """Tier 2: tokens by node per config, the configs side by side, and where they differ."""
        def pct(v):
            return "-" if v is None else f"{v:.1%}"

        def usd(v):
            return "-" if v is None else f"${v:.4f}"

        def sec(v):
            return "-" if v is None else f"{v:.2f}s"

        for name, cfg in board["configs"].items():
            rows = [[node, t["calls"], t["input_tokens"], t["cached_input_tokens"], t["output_tokens"],
                     t["reasoning_tokens"], sec((cfg["latency"]["by_node"].get(node) or {}).get("p50"))]
                    for node, t in sorted(cfg["tokens_by_node"].items())]
            if rows:
                self.print_table(rows, title=f"Tokens by node: {name}",
                                 columns=["Node", "Calls", "Input", "Cached", "Output", "Reasoning", "p50"])

        rows = [[r["config"], f"{board['configs'][r['config']]['completed_cases']}/"
                 f"{board['configs'][r['config']]['planned_cases']}", pct(r["accuracy"]),
                 pct(r["answerability_precision"]), pct(r["answerability_recall"]), usd(r["cost_total"]),
                 usd(r["cost_per_question"]), sec(r["latency_p50"]), sec(r["latency_p95"]), r["retries"],
                 pct(r["determinism"])] for r in board["comparison"]["configs"]]
        self.print_table(rows, title="Tier 2 scoreboard", columns=[
            "Config", "Cases", "Accuracy", "Ans. P", "Ans. R", "Cost", "$/question", "p50", "p95",
            "Retries", "Determinism"])
        differences = board["comparison"]["differences"]
        if differences:
            names = list(board["configs"])
            self.print_table([[d["id"], d["role"], *(d["outcomes"].get(n, "-") for n in names)]
                              for d in differences],
                             title="Questions the configs disagree on", columns=["ID", "Role", *names])
        stopped = f"  STOPPED: {board['stopped']}" if board.get("stopped") else ""
        self.console.print(f"Spent ${board['spent']:.4f} of ${board['max_cost']:.2f} cap{stopped}")

    def export_benchmark_report(self, report: Dict[str, Any], path: Path) -> None:
        """Writes the benchmark report as JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
        self.console.print(f"\n[bold green]Report written to {escape(str(path))}[/bold green]")

    # ------------------------------------------------------------------
    # Indexing (indexing.py)
    # ------------------------------------------------------------------
    def print_indexing_start(self, path: str) -> None:
        self.console.print(f"[bold blue]Indexing schema to:[/bold blue] {escape(str(path))}")

    def print_indexing_error(self, ds_id: str, error: str) -> None:
        self.console.print(f"[red]Failed to index {escape(str(ds_id))}: {escape(str(error))}[/red]")

    def print_indexing_complete(self) -> None:
        self.console.print("\n[bold green]Indexing complete![/bold green]")

    def print_indexing_summary(self, stats: List[Dict[str, Any]]) -> None:
        if not stats:
            return

        table = Table(title="Indexing Summary", show_header=True, header_style="bold magenta", expand=True)
        table.add_column("Datasource", style="cyan")
        table.add_column("Tables", justify="right")
        table.add_column("Columns", justify="right")
        table.add_column("Examples", justify="right")

        total_tables = 0
        total_cols = 0
        total_examples = 0

        for s in stats:
            t = s.get("tables", 0)
            c = s.get("columns", 0)
            e = s.get("examples", 0)

            total_tables += t
            total_cols += c
            total_examples += e

            table.add_row(Text(str(s.get("id", "Unknown"))), str(t), str(c), str(e))

        table.add_row(
            "[bold]TOTAL[/bold]",
            f"[bold]{total_tables}[/bold]",
            f"[bold]{total_cols}[/bold]",
            f"[bold]{total_examples}[/bold]",
            style="green",
        )

        self.console.print("\n")
        self.console.print(table)

    def create_progress(self):
        from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=self.console,
        )

    # ------------------------------------------------------------------
    # Visualization (visualize.py)
    # ------------------------------------------------------------------
    def print_graph_saved(self, path: str) -> None:
        import os

        abs_path = os.path.abspath(path)
        link = Text(abs_path, style=Style(bold=True, underline=True, link=f"file:///{abs_path}"))
        self.console.print("Graph visualization saved to: ", link)

    def print_graph_save_error(self, error: str) -> None:
        self.console.print(f"[bold red]Failed to save graph image:[/bold red] {escape(str(error))}")

    def track(self, sequence: Iterable[Any], description: str = "Working...", total: Optional[float] = None):
        from rich.progress import track

        return track(sequence, description=description, total=total, console=self.console)

