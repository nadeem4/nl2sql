"""Tier 1 runs with curated answers off.

Tier 1 scores hand-written plans through the code nodes, and a verified
answer has no plan, so a verified query must never answer a gold question
there. The run turns curated answers off on its context and restores them
afterwards.
"""
from types import SimpleNamespace

from nl2sql.evaluation import tier1


def test_tier1_runs_with_curated_answers_off_and_restores_them(monkeypatch):
    seen = {}

    class _Runner:
        def __init__(self, config, ctx, workers, before_case):
            self.ctx = ctx

        def run_dataset(self):
            seen["curated"] = self.ctx.curated
            return "result"

    monkeypatch.setattr(tier1, "BenchmarkRunner", _Runner)
    monkeypatch.setattr(tier1, "load_gold_plans", lambda: {})
    monkeypatch.setattr(tier1, "load_gold_dataset", lambda _path: [])
    configured = object()
    ctx = SimpleNamespace(curated=configured,
                          llm_registry=SimpleNamespace(replace_llms=lambda _llms: None))

    assert tier1.run_tier1(ctx) == "result"

    assert seen["curated"] is None
    assert ctx.curated is configured
