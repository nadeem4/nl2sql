import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from nl2sql.context import current_datasource_id
from nl2sql.metrics import LATENCY_LOG
from nl2sql.reporting import ConsolePresenter
from nl2sql.callbacks.node_context import current_node_run_id
from nl2sql.callbacks.node_metrics import NodeMetrics


class NodeHandler:
    def __init__(self, presenter: ConsolePresenter):
        self.presenter = presenter

        self.starts: Dict[str, float] = {}
        self.node_names: Dict[str, str] = {}
        self.parents: Dict[str, str] = {}

        self.primary_run_id: Dict[str, str] = {}

        self.tree: Dict[str, List[str]] = {}
        self.node_metrics: Dict[str, NodeMetrics] = {}

        self.node_active_count: Dict[str, int] = {}
        self.ds_tokens: Dict[str, Any] = {}
        self.run_tokens: Dict[str, Any] = {}

    def on_chain_start(
        self,
        run_id: str,
        parent_run_id: Optional[str],
        node_name: Optional[str],
        inputs: Dict[str, Any],
    ):
        if not node_name:
            return

        now = time.perf_counter()

        self.node_names[run_id] = node_name
        self.parents[run_id] = parent_run_id or "root"
        self.starts[run_id] = now

        if node_name not in self.primary_run_id:
            self.primary_run_id[node_name] = run_id

            parent_primary = "root"
            if parent_run_id and parent_run_id in self.node_names:
                parent_node = self.node_names[parent_run_id]
                parent_primary = self.primary_run_id.get(parent_node, "root")

            self.tree.setdefault(parent_primary, []).append(run_id)
            self.tree.setdefault(run_id, [])

            self.node_metrics[run_id] = NodeMetrics(
                datasource_id=current_datasource_id.get()
            )

        primary_id = self.primary_run_id[node_name]
        m = self.node_metrics[primary_id]
        m.start_time = min(m.start_time, now)

        ds_id = None
        if isinstance(inputs, dict):
            ds_id = inputs.get("selected_datasource_id") or inputs.get("datasource_id")
        if ds_id:
            tok = current_datasource_id.set(ds_id)
            self.ds_tokens[run_id] = tok

        tok = current_node_run_id.set(primary_id)
        self.run_tokens[run_id] = tok

        self.node_active_count[node_name] = self.node_active_count.get(node_name, 0) + 1
        if self.node_active_count[node_name] == 1:
            self.presenter.update_interactive_status(
                f"[bold blue]{node_name}[/bold blue] Working..."
            )

    def on_chain_end(self, run_id: str):
        node = self.node_names.get(run_id)
        start = self.starts.get(run_id)

        if not node or start is None:
            return

        end = time.perf_counter()
        primary_id = self.primary_run_id[node]
        m = self.node_metrics[primary_id]

        m.end_time = max(m.end_time, end)
        m.duration = m.end_time - m.start_time

        LATENCY_LOG.append(
            {
                "node": node,
                "duration": end - start,
                "datasource_id": current_datasource_id.get(),
                "run_id": run_id,
                "parent_run_id": self.parents.get(run_id),
            }
        )

        self.node_active_count[node] -= 1
        if self.node_active_count[node] == 0:
            token_str = f" | {m.total_tokens} tok" if m.total_tokens > 0 else ""
            self.presenter.console.print(
                f"[green]✔[/green] [bold]{node}[/bold] Completed ({m.duration:.2f}s{token_str})"
            )
            self.presenter.update_interactive_status("Thinking...")

        tok = self.run_tokens.pop(run_id, None)
        if tok:
            current_node_run_id.reset(tok)

        ds_tok = self.ds_tokens.pop(run_id, None)
        if ds_tok:
            current_datasource_id.reset(ds_tok)

    def on_chain_error(self, run_id: str, error: BaseException):
        node = self.node_names.get(run_id)
        start = self.starts.get(run_id)
        end = time.perf_counter()

        if node:
            primary_id = self.primary_run_id[node]
            m = self.node_metrics[primary_id]
            m.start_time = min(m.start_time, start or end)
            m.end_time = max(m.end_time, end)
            m.duration = m.end_time - m.start_time
            m.error = str(error)

        LATENCY_LOG.append(
            {
                "node": node or "unknown",
                "duration": (end - start) if start else 0.0,
                "error": str(error),
                "datasource_id": current_datasource_id.get(),
                "run_id": run_id,
                "parent_run_id": self.parents.get(run_id),
            }
        )

        if node:
            self.node_active_count[node] -= 1
            self.presenter.console.print(
                f"[red]✘[/red] [bold]{node}[/bold] Failed: {error}"
            )
            self.presenter.update_interactive_status("Error encountered...")

        tok = self.run_tokens.pop(run_id, None)
        if tok:
            current_node_run_id.reset(tok)

        ds_tok = self.ds_tokens.pop(run_id, None)
        if ds_tok:
            current_datasource_id.reset(ds_tok)

    def get_performance_tree(self):
        return self.tree, self.node_metrics, self.node_names