from nl2sql.core.callbacks.node_handlers import NodeHandler
from nl2sql.core.callbacks.token_handler import TokenHandler
from langchain_core.callbacks import BaseCallbackHandler
from nl2sql.reporting import ConsolePresenter
from typing import Dict, Any
from langchain_core.outputs import LLMResult

class PipelineMonitorCallback(BaseCallbackHandler):
    def __init__(self, presenter: ConsolePresenter):
        self.node_handler = NodeHandler(presenter)
        self.tokens = TokenHandler(self.node_handler.node_metrics)

    def on_chain_start(self, serialized: Dict[str, Any], inputs: Dict[str, Any], **kwargs: Any) -> Any:
        node_name = kwargs.get("metadata", {}).get("langgraph_node")
        run_id = str(kwargs.get("run_id"))
        parent_run_id = kwargs.get("parent_run_id")
        parent_run_id = str(parent_run_id) if parent_run_id else None
        self.node_handler.on_chain_start(run_id, parent_run_id, node_name, inputs)

    def on_chain_end(self, outputs: Dict[str, Any], **kwargs: Any) -> Any:
        run_id = str(kwargs.get("run_id"))
        self.node_handler.on_chain_end(run_id)

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> Any:
        run_id = str(kwargs.get("run_id"))
        self.node_handler.on_chain_error(run_id, error)

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> Any:
        tags = kwargs.get("tags", [])
        agent_name = next((t for t in tags if not t.startswith("seq:") and not t.startswith("langsmith:")), "unknown")
        self.tokens.on_llm_end(response, agent_name=agent_name)

    def get_status_tree(self):
        self.node_handler.print_tree()

    def get_performance_tree(self):
        return self.node_handler.get_performance_tree()