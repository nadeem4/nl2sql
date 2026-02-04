from nl2sql_api.models.datasource import DatasourceRequest
from nl2sql_api.models.llm import LLMRequest
from nl2sql_api.services.datasource import DatasourceService
from nl2sql_api.services.llm import LLMService


class FakeEngine:
    def __init__(self) -> None:
        self._datasources = []
        self._llms = {}

    def add_datasource(self, config):
        self._datasources.append(config["id"])

    def list_datasources(self):
        return list(self._datasources)

    def configure_llm(self, config):
        name = config.get("name", "default")
        self._llms[name] = config

    def list_llms(self):
        return {
            name: {"name": cfg.get("name", name)}
            for name, cfg in self._llms.items()
        }

    def get_llm(self, name):
        if name in self._llms:
            return self._llms[name]
        return self._llms.get("default")


def test_datasource_service_live_update():
    engine = FakeEngine()
    service = DatasourceService(engine)

    service.add_datasource(DatasourceRequest(config={"id": "ds-1"}))

    assert "ds-1" in service.list_datasources()


def test_llm_service_live_update():
    engine = FakeEngine()
    service = LLMService(engine)

    service.configure_llm(
        LLMRequest(config={"name": "custom", "provider": "openai", "model": "gpt"})
    )

    llms = service.list_llms()
    assert "custom" in llms
    assert service.get_llm("custom")["name"] == "custom"
