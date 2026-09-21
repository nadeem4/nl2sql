from nl2sql.services.callbacks.node_handlers import NodeHandler
from nl2sql.services.callbacks.token_handler import read_usage
from langchain_core.callbacks import BaseCallbackHandler
from nl2sql.services.callbacks.presenter import PresenterProtocol
from typing import Dict, Any
from langchain_core.outputs import LLMResult

class PipelineMonitorCallback(BaseCallbackHandler):
    """Callback handler for CLI progress, node-duration metrics and audit events.

    Token usage is not counted here: ``run_with_graph`` attaches
    ``TokenUsageCallback`` to every run, which records it into
    ``QueryResult.usage`` and the ``nl2sql.token.usage`` counter.
    """
    
    def __init__(self, presenter: PresenterProtocol):
        from nl2sql.common.settings import settings
        from nl2sql.common.metrics import configure_metrics
        
        configure_metrics(
            exporter_type=settings.observability_exporter,
            otlp_endpoint=settings.otlp_endpoint
        )
        
        self.node_handler = NodeHandler(presenter)

    def on_chain_start(self, serialized: Dict[str, Any], inputs: Dict[str, Any], **kwargs: Any) -> Any:
        """Called when a chain starts."""
        node_name = kwargs.get("metadata", {}).get("langgraph_node")
        run_id = str(kwargs.get("run_id"))
        parent_run_id = kwargs.get("parent_run_id")
        parent_run_id = str(parent_run_id) if parent_run_id else None
        self.node_handler.on_chain_start(run_id, parent_run_id, node_name, inputs)

    def on_chain_end(self, outputs: Dict[str, Any], **kwargs: Any) -> Any:
        """Called when a chain ends."""
        run_id = str(kwargs.get("run_id"))
        self.node_handler.on_chain_end(run_id)

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> Any:
        """Called when a chain errors."""
        run_id = str(kwargs.get("run_id"))
        self.node_handler.on_chain_error(run_id, error)

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> Any:
        """Called when an LLM ends."""
        from nl2sql.common.event_logger import event_logger
        from nl2sql.common.logger import _trace_id_ctx, _tenant_id_ctx
        
        tags = kwargs.get("tags", [])
        agent_name = next((t for t in tags if not t.startswith("seq:") and not t.startswith("langsmith:")), "unknown")
        
        text_output = ""
        model_name = "unknown"

        if response.generations:
            gen = response.generations[0][0]
            text_output = gen.text

        if response.llm_output:
            model_name = response.llm_output.get("model_name", "unknown")
        token_usage = read_usage(response) or {}
            
        audit_payload = {
            "agent": agent_name,
            "model": model_name,
            "response_snippet": text_output[:1000], 
            "token_usage": token_usage
        }
        
        event_logger.log_event(
            event_type="llm_interaction", 
            payload=audit_payload,
            trace_id=_trace_id_ctx.get(),
            tenant_id=_tenant_id_ctx.get()
        )


    def get_status_tree(self):
        self.node_handler.print_tree()

    def get_performance_tree(self):
        return self.node_handler.get_performance_tree()
