from dataclasses import asdict
from typing import Any, Dict

from nl2sql import NL2SQL, UserContext


class LLMService:
    def __init__(self, engine: NL2SQL):
        self.engine = engine

    def list_llms(self) -> Dict[str, Dict[str, Any]]:
        """Each agent and the model it runs on, as ``engine.llm.list()`` reports it."""
        return self.engine.llm.list()

    def get_llm(self, agent: str) -> Dict[str, Any]:
        """One agent's entry; ``ValueError`` for an unknown agent."""
        return self.engine.llm.get(agent)

    def replace_llm(self, section: Dict[str, Any], by: UserContext) -> Dict[str, Any]:
        """Replace the whole ``llm`` section; ``ConfigError`` when it is not valid. Returns the change."""
        return asdict(self.engine.llm.replace(section, by=by, via="api"))
