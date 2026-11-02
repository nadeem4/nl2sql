"""Service-layer tests (ported from packages/api/test_services_updates.py)."""

from nl2sql import LLMChange, UserContext
from nl2sql_api.services.llm import LLMService


class FakeLLMs:
    def __init__(self) -> None:
        self.replaced = []

    def list(self):
        return {"default": {"provider": "openai", "model": "gpt", "temperature": 0.0, "inherited": False}}

    def get(self, agent):
        return self.list()[agent]

    def replace(self, section, by=None, via="python"):
        self.replaced.append((section, by, via))
        return LLMChange(by={"via": via}, changes={"default": {"old": "openai/gpt", "new": "openai/gpt-2"}})


class FakeEngine:
    def __init__(self) -> None:
        self.llm = FakeLLMs()


def test_llm_service_replaces_the_section_through_the_engine():
    engine = FakeEngine()
    service = LLMService(engine)

    change = service.replace_llm({"provider": "openai", "model": "gpt-2"}, by=UserContext(roles=["admin"]))

    assert change["changes"]["default"]["new"] == "openai/gpt-2"
    assert engine.llm.replaced[0][2] == "api"
    assert service.get_llm("default")["model"] == "gpt"
