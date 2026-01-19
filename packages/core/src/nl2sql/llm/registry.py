from nl2sql.secrets import SecretManager
from pathlib import Path
from langchain_openai import ChatOpenAI
from .models import AgentConfig
from typing import Dict

class LLMRegistry:

    def __init__(self, secret_manager: SecretManager ):
        self.secret_manager = secret_manager
        self.llms = {}

    def register_llms(self, config: Dict[str, AgentConfig]):
        for agent in config.values():
            self.register_llm(agent)


    def register_llm(self, agent: AgentConfig):
        if agent.provider == "openai":
            self.register_openai_llm(agent)
        else:
            raise ValueError(f"Unsupported LLM provider: {agent.provider}")

    def register_openai_llm(self, agent: AgentConfig):
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError("langchain-openai is not installed. Please install it using 'pip install langchain-openai'")

        api_key = self.secret_manager.resolve_object(agent.api_key)
        llm = ChatOpenAI(model=agent.model, api_key=api_key,temperature=agent.temperature, tags=[agent.name], seed=42)
        self.llms[agent.name] = llm


    
    def get_llm(self, name: str) -> ChatOpenAI:
        if name not in self.llms:
            return self.llms['default']
        return self.llms[name]

    