"""Tier 1 runs with curated answers off.

Tier 1 scores hand-written plans through the code nodes, and a verified
answer has no plan, so a verified query must never answer a gold question
there. The run turns curated answers off on its context and restores them
afterwards. Refusals are scored against the generic message, so the run pins
``policies.refusal_names_tables`` off on the runtime's config and puts it back.
"""
from types import SimpleNamespace

from nl2sql.evaluation import tier1
from nl2sql.testing.runtime import fake_runtime


def test_tier1_runs_with_curated_answers_off_and_restores_them(monkeypatch):
    seen = {}

    class _Runner:
        def __init__(self, config, ctx, workers, before_case):
            self.ctx = ctx

        def run_dataset(self):
            seen["curated"] = self.ctx.curated
            seen["names_tables"] = self.ctx.config.policies.refusal_names_tables
            return "result"

    monkeypatch.setattr(tier1, "BenchmarkRunner", _Runner)
    monkeypatch.setattr(tier1, "load_gold_plans", lambda: {})
    monkeypatch.setattr(tier1, "load_gold_dataset", lambda _path: [])
    configured = object()
    ctx = fake_runtime(policies={"refusal_names_tables": True}, curated=configured,
                       llm_registry=SimpleNamespace(replace_llms=lambda _llms: None))

    assert tier1.run_tier1(ctx) == "result"

    assert seen["curated"] is None
    assert seen["names_tables"] is False
    assert ctx.curated is configured
    assert ctx.config.policies.refusal_names_tables is True
