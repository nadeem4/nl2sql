from typing import Dict, Any, Optional
from nl2sql import NL2SQL
from nl2sql_api.models.llm import LLMRequest, LLMResponse


class LLMService:
    def __init__(self, engine: NL2SQL):
        self.engine = engine

    def configure_llm(self, request: LLMRequest) -> LLMResponse:
        """Configure an LLM programmatically."""
        self.engine.configure_llm(request.config)
        return LLMResponse(
            success=True,
            message=f"LLM '{request.config.get('name', 'default')}' configured successfully",
            llm_name=request.config.get('name', 'default')
        )

    def list_llms(self) -> list:
        """List all configured LLMs."""
        return self.engine.list_llms()

    def get_llm(self, llm_name: str) -> dict:
        """Get details of a specific LLM."""
        return self.engine.get_llm(llm_name)