"""``GET /api/pipeline``: what the Pipeline page reads before any run.

The page lists every step of a run, marks the five a model decides, and shows
the model each of those is configured to use. All of that comes from here, so
the page is useful on arrival rather than only after a question.
"""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from nl2sql import NL2SQL
from nl2sql.cli.demo.playground.app import build_app
from nl2sql.cli.demo.playground.hosted import Hosted
from nl2sql.llm.providers import LLM_AGENTS


class _Engine(NL2SQL):
    """The facade with a scripted LLM configuration and nothing else."""

    def __init__(self, llms=None):
        self._llms = llms if llms is not None else {
            "default": {"provider": "openai", "model": "gpt-5.4", "temperature": 0.0},
        }

    def list_llms(self):
        return self._llms

    def list_datasources(self):
        return ["chinook"]


class _Broken(_Engine):
    def list_llms(self):
        raise RuntimeError("no registry here")


def _client(engine=None, hosted=False):
    app = build_app(engine or _Engine(), questions=[], roles=["admin"],
                    mode="hosted" if hosted else "replay", dataset="chinook",
                    hosted=Hosted(enabled=hosted))
    return TestClient(app)


def test_the_route_lists_every_step_in_order_with_the_model_steps_marked():
    body = _client().get("/api/pipeline").json()

    steps = body["steps"]
    assert [s["node"] for s in steps][0] == "datasource_resolver"
    assert [s["node"] for s in steps][-1] == "answer_synthesizer"
    model_steps = [s["node"] for s in steps if s["kind"] == "model"]
    assert model_steps == list(LLM_AGENTS)
    # Every step says what it is and what it decides.
    assert all(s["label"] and s["does"] for s in steps)
    # And the SQL agent's own nodes are nested under it.
    assert {s["node"] for s in steps if s["parent"] == "sql_agent"} >= {"ast_planner", "generator"}


def test_each_model_step_carries_the_model_it_is_configured_to_use():
    engine = _Engine({
        "default": {"provider": "openai", "model": "gpt-5.4"},
        "astplanner": {"provider": "anthropic", "model": "claude-opus-5"},
    })

    steps = {s["node"]: s for s in _client(engine).get("/api/pipeline").json()["steps"]}

    assert (steps["ast_planner"]["provider"], steps["ast_planner"]["model"]) == ("anthropic", "claude-opus-5")
    assert (steps["decomposer"]["provider"], steps["decomposer"]["model"]) == ("openai", "gpt-5.4")
    assert steps["generator"]["provider"] is None and steps["generator"]["model"] is None


def test_the_route_answers_on_the_hosted_demo_too():
    body = _client(hosted=True).get("/api/pipeline").json()

    assert [s["node"] for s in body["steps"] if s["kind"] == "model"] == list(LLM_AGENTS)


def test_an_engine_that_cannot_report_its_models_still_lists_the_steps():
    body = _client(_Broken()).get("/api/pipeline").json()

    assert len(body["steps"]) == 14
    assert all(s["model"] is None for s in body["steps"])


def test_the_route_never_carries_a_key():
    engine = _Engine({"default": {"provider": "openai", "model": "gpt-5.4",
                                  "api_key": "sk-proj-shouldnotbehere0000000000"}})

    text = _client(engine).get("/api/pipeline").text

    assert "sk-proj" not in text
    assert "api_key" not in text
