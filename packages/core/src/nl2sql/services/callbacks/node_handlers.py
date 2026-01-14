import time
from typing import Dict, Any, List, Optional

from nl2sql.common.context import current_datasource_id
from nl2sql.common.metrics import LATENCY_LOG, node_duration_histogram
from nl2sql.reporting import ConsolePresenter
from nl2sql.services.callbacks.node_context import current_node_run_id
from nl2sql.services.callbacks.node_metrics import NodeMetrics


class NodeHandler:
    def __init__(self, presenter: ConsolePresenter):
        self.presenter = presenter

        self.run_start: Dict[str, float] = {}
        self.run_to_node: Dict[str, str] = {}
        self.run_parent: Dict[str, Optional[str]] = {}

        self.primary_run_id: Dict[str, str] = {}

        self.tree: Dict[str, List[str]] = {"root": []}
        self.node_metrics: Dict[str, NodeMetrics] = {}

        self.node_active_count: Dict[str, int] = {}
        self.run_ctx_tokens: Dict[str, Any] = {}
        self.ds_ctx_tokens: Dict[str, Any] = {}

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

        raw_node = node_name
        display_node = raw_node

        if raw_node == "execution_branch":
            if isinstance(inputs, dict):
                q = inputs.get("user_query")
                if q:
                    display_node = f"execution_branch ({q})"

        self.run_start[run_id] = now
        self.run_to_node[run_id] = display_node
        self.run_parent[run_id] = parent_run_id

        if display_node not in self.primary_run_id:
            self.primary_run_id[display_node] = run_id

            parent_primary = "root"
            if parent_run_id and parent_run_id in self.run_to_node:
                parent_node = self.run_to_node[parent_run_id]
                parent_primary = self.primary_run_id.get(parent_node, "root")

            self.tree.setdefault(parent_primary, []).append(run_id)
            self.tree.setdefault(run_id, [])

            self.node_metrics[run_id] = NodeMetrics(
                start_time=now,
                end_time=now,
                datasource_id=current_datasource_id.get(),
            )

        primary_id = self.primary_run_id[display_node]
        metrics = self.node_metrics[primary_id]
        metrics.start_time = min(metrics.start_time, now)

        ds_id = None
        if isinstance(inputs, dict):
            ds_id = inputs.get("selected_datasource_id") or inputs.get("datasource_id")

        if ds_id:
            tok = current_datasource_id.set(ds_id)
            self.ds_ctx_tokens[run_id] = tok

        tok = current_node_run_id.set(primary_id)
        self.run_ctx_tokens[run_id] = tok

        self.node_active_count[display_node] = (
            self.node_active_count.get(display_node, 0) + 1
        )

        if self.node_active_count[display_node] == 1:
            self.presenter.update_interactive_status(
                f"[bold blue]{display_node}[/bold blue] Working..."
            )

    def on_chain_end(self, run_id: str):
        node = self.run_to_node.get(run_id)
        start = self.run_start.get(run_id)

        if not node or start is None:
            return

        end = time.perf_counter()
        primary_id = self.primary_run_id[node]
        metrics = self.node_metrics[primary_id]

        metrics.end_time = max(metrics.end_time, end)
        metrics.duration = metrics.end_time - metrics.start_time


        LATENCY_LOG.append(
            {
                "node": node,
                "duration": end - start,
                "datasource_id": current_datasource_id.get(),
                "run_id": run_id,
                "parent_run_id": self.run_parent.get(run_id),
            }
        )
        
        # OTeL Instrumentation
        node_duration_histogram.record(
            end - start,
            attributes={
                "node": node,
                "datasource_id": str(current_datasource_id.get() or "none"),
            }
        )

        self.node_active_count[node] -= 1
        if self.node_active_count[node] == 0:
            tok_str = f" | {metrics.total_tokens} tok" if metrics.total_tokens else ""
            self.presenter.console.print(
                f"[green]OK[/green] [bold]{node}[/bold] Completed ({metrics.duration:.2f}s{tok_str})"
            )
            self.presenter.update_interactive_status("Thinking...")

        tok = self.run_ctx_tokens.pop(run_id, None)
        if tok:
            current_node_run_id.reset(tok)

        ds_tok = self.ds_ctx_tokens.pop(run_id, None)
        if ds_tok:
            current_datasource_id.reset(ds_tok)

    def on_chain_error(self, run_id: str, error: BaseException):
        node = self.run_to_node.get(run_id)
        start = self.run_start.get(run_id)
        end = time.perf_counter()

        if node:
            primary_id = self.primary_run_id[node]
            metrics = self.node_metrics[primary_id]
            metrics.end_time = max(metrics.end_time, end)
            metrics.duration = metrics.end_time - metrics.start_time
            metrics.error = str(error)

            self.presenter.console.print(
                f"[red]X[/red] [bold]{node}[/bold] Failed: {error}"
            )
            self.presenter.update_interactive_status("Error encountered...")

        tok = self.run_ctx_tokens.pop(run_id, None)
        if tok:
            current_node_run_id.reset(tok)

        ds_tok = self.ds_ctx_tokens.pop(run_id, None)
        if ds_tok:
            current_datasource_id.reset(ds_tok)

    def get_performance_tree(self):
        return (
            self.tree,
            self.node_metrics,
            {rid: self.run_to_node[rid] for rid in self.node_metrics},
        )
