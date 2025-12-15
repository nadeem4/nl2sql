import time
from typing import Dict, Any, Optional
from langchain_core.callbacks import BaseCallbackHandler
from nl2sql.reporting import ConsolePresenter

class StatusCallback(BaseCallbackHandler):
    """
    Callback updates the CLI status spinner and prints checkmarks on node completion.
    """
    def __init__(self, presenter: ConsolePresenter):
        self.presenter = presenter
        self.starts: Dict[str, float] = {}

    def on_chain_start(self, serialized: Dict[str, Any], inputs: Dict[str, Any], **kwargs: Any) -> Any:
        metadata = kwargs.get("metadata", {})
        node_name = metadata.get("langgraph_node")
        run_name = kwargs.get("name")
        run_id = kwargs.get("run_id")

        if node_name and run_name == node_name:
            self.starts[str(run_id)] = time.perf_counter()
            self.presenter.update_interactive_status(f"[bold blue]{node_name}[/bold blue] Working...")

    def on_chain_end(self, outputs: Dict[str, Any], **kwargs: Any) -> Any:
        metadata = kwargs.get("metadata", {})
        node_name = metadata.get("langgraph_node")
        run_name = kwargs.get("name")
        run_id = kwargs.get("run_id")
        run_key = str(run_id)

        if node_name and run_name == node_name:
            start_time = self.starts.pop(run_key, None)
            duration_str = ""
            if start_time:
                duration = time.perf_counter() - start_time
                duration_str = f" ({duration:.2f}s)"
            
            self.presenter.console.print(f"[green]✔[/green] [bold]{node_name}[/bold] Completed{duration_str}")
            
            self.presenter.update_interactive_status("Thinking...")

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> Any:
        metadata = kwargs.get("metadata", {})
        node_name = metadata.get("langgraph_node")
        run_name = kwargs.get("name")
        run_id = kwargs.get("run_id")
        
        if node_name and run_name == node_name:
            self.starts.pop(str(run_id), None)
            self.presenter.console.print(f"[red]✘[/red] [bold]{node_name}[/bold] Failed: {error}")
            self.presenter.update_interactive_status("Error encountered...")
