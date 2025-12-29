from langchain_core.outputs import LLMResult
from nl2sql.core.metrics import TOKEN_LOG
from nl2sql.core.context import current_datasource_id
from nl2sql.core.callbacks.node_context import current_node_run_id
from nl2sql.core.callbacks.node_metrics import NodeMetrics


class TokenHandler:
    def __init__(self, node_metrics: dict[str, NodeMetrics]):
        self.node_metrics = node_metrics

    def on_llm_end(self, response: LLMResult, agent_name: str = "unknown", model_name: str = "unknown"):
        usage = None
        if response and response.llm_output:
            usage = response.llm_output.get("token_usage") or response.llm_output.get("usage")

        if not usage:
            return

        p = usage.get("prompt_tokens") or usage.get("input_tokens", 0)
        c = usage.get("completion_tokens") or usage.get("output_tokens", 0)
        t = usage.get("total_tokens") or (p + c)

        run_id = current_node_run_id.get()

        TOKEN_LOG.append(
            {
                "agent": agent_name,
                "model": model_name,
                "datasource_id": current_datasource_id.get(),
                "prompt_tokens": p,
                "completion_tokens": c,
                "total_tokens": t,
                "run_id": run_id,
            }
        )

        if run_id and run_id in self.node_metrics:
            m = self.node_metrics[run_id]
            m.prompt_tokens += int(p)
            m.completion_tokens += int(c)
            m.total_tokens += int(t)
