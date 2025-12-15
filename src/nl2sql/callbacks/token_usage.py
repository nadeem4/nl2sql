from __future__ import annotations

import time
from typing import Dict, List, Any
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

class TokenUsageCallback(BaseCallbackHandler):
    """
    Callback to track token usage for LLM calls.
    """
    def __init__(self, agent_name: str, model_name: str):
        self.agent_name = agent_name
        self.model_name = model_name
        self.start_time = 0.0

    def on_llm_start(
        self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any
    ) -> Any:
        self.start_time = time.perf_counter()

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> Any:    
        if not response.llm_output:
            return

        usage = response.llm_output.get("token_usage")
        if usage:
            from nl2sql.context import current_datasource_id
            from nl2sql.metrics import TOKEN_LOG
            
            p_tokens = usage.get("prompt_tokens") or usage.get("input_tokens", 0)
            c_tokens = usage.get("completion_tokens") or usage.get("output_tokens", 0)
            t_tokens = usage.get("total_tokens", 0)
            
            TOKEN_LOG.append({
                "agent": self.agent_name,
                "model": self.model_name,
                "datasource_id": current_datasource_id.get(),
                "prompt_tokens": p_tokens,
                "completion_tokens": c_tokens,
                "total_tokens": t_tokens,
            })
