"""``engine.llm.recorded_with()``: which model the ``replay`` provider's recordings came from."""
from types import SimpleNamespace

from nl2sql.api.llm_api import LLMAPI
from nl2sql.llm.replay import ReplayStore


def _api(store):
    registry = SimpleNamespace(load=lambda kind, name: SimpleNamespace(store=store))
    return LLMAPI(SimpleNamespace(registry=registry))


def test_recorded_with_names_the_provider_and_model_of_the_recordings():
    assert _api(ReplayStore(provider="openai", model="gpt-x")).recorded_with() == {"provider": "openai", "model": "gpt-x"}


def test_recorded_with_is_none_when_the_recordings_name_no_model():
    assert _api(ReplayStore()).recorded_with() is None
