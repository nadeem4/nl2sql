from nl2sql.services.callbacks.node_handlers import NodeHandler
from nl2sql.services.callbacks.token_handler import TokenHandler
from langchain_core.callbacks import BaseCallbackHandler
from nl2sql.reporting import ConsolePresenter
from typing import Dict, Any
from langchain_core.outputs import LLMResult

class PipelineMonitorCallback(BaseCallbackHandler):
    def __init__(self, presenter: ConsolePresenter):
        from nl2sql.common.settings import settings
        from nl2sql.common.metrics import configure_metrics
        
        # One-time setup of metrics based on settings
        configure_metrics(
            exporter_type=settings.observability_exporter,
            otlp_endpoint=settings.otlp_endpoint
        )
        
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
        from nl2sql.common.event_logger import event_logger
        from nl2sql.common.logger import _trace_id_ctx, _tenant_id_ctx
        
        tags = kwargs.get("tags", [])
        agent_name = next((t for t in tags if not t.startswith("seq:") and not t.startswith("langsmith:")), "unknown")
        
        # Capture audit event
        # Assuming single generation per call for simplicity in audit log
        text_output = ""
        model_name = "unknown"
        token_usage = {}
        
        if response.generations:
            gen = response.generations[0][0]
            text_output = gen.text
            
        if response.llm_output:
            model_name = response.llm_output.get("model_name", "unknown")
            token_usage = response.llm_output.get("token_usage", {})
            
        audit_payload = {
            "agent": agent_name,
            "model": model_name,
            "response_snippet": text_output[:1000], # Trucate for sanity, full content maybe too big?
            "token_usage": token_usage
        }
        
        # Pull context directly from vars since we are in the same thread execution context
        event_logger.log_event(
            event_type="llm_interaction", 
            payload=audit_payload,
            trace_id=_trace_id_ctx.get(),
            tenant_id=_tenant_id_ctx.get()
        )
        
        self.tokens.on_llm_end(response, agent_name=agent_name)

    def get_status_tree(self):
        self.node_handler.print_tree()

    def get_performance_tree(self):
        return self.node_handler.get_performance_tree()
