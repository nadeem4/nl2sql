from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nl2sql.policies import UserContext
from nl2sql.runtime import Runtime
from nl2sql.testing.config import make_demo_config
from nl2sql.pipeline.nodes.datasource_resolver.schemas import (
    DatasourceResolverResponse,
    ResolvedDatasource,
)
from nl2sql.pipeline.nodes.decomposer.node import DecomposerNode
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse
from nl2sql.pipeline.state import GraphState


# The node under test builds an LLM client in its constructor, so these cannot
# run without a real key. See docs/testing/architecture.md.
pytestmark = pytest.mark.llm


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _load_sample_questions(root: Path) -> dict[str, list[str]]:
    """The demo's guided questions, by datasource (``nl2sql.datasets.demo``)."""
    from nl2sql.datasets.demo import DEMO_QUESTIONS_BY_DATASOURCE

    return {ds: list(questions) for ds, questions in DEMO_QUESTIONS_BY_DATASOURCE.items()}


@pytest.fixture(scope="module")
def demo_env() -> SimpleNamespace:
    root = _project_root()

    tmp_dir = Path(tempfile.mkdtemp(prefix="decomposer_real_data_"))

    monkeypatch = pytest.MonkeyPatch()
    collection_name = f"itest_decomposer_{uuid.uuid4().hex}"
    vector_store_path = tmp_dir / "chroma"
    schema_store_path = tmp_dir / "schema_store.db"

    ctx = Runtime.from_config(make_demo_config(tmp_dir))

    env = SimpleNamespace(ctx=ctx, root=root, tmp_dir=tmp_dir)
    try:
        yield env
    finally:
        monkeypatch.undo()
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def resolver_response(demo_env) -> DatasourceResolverResponse:
    questions = _load_sample_questions(demo_env.root)
    datasource_ids = list(questions.keys())
    resolved = [
        ResolvedDatasource(datasource_id=ds_id, metadata={"datasource_id": ds_id})
        for ds_id in datasource_ids
    ]
    return DatasourceResolverResponse(
        resolved_datasources=resolved,
        allowed_datasource_ids=datasource_ids,
        unsupported_datasource_ids=[],
    )


def _build_queries(questions: dict[str, list[str]]) -> list[str]:
    """Single-intent and two-intent queries, built from the demo's own questions.

    Keyed off nothing: the demo ships one datasource today and may ship more
    later, so this reads whatever ``sample_questions.demo.yaml`` contains.
    """
    queries: list[str] = []
    for items in questions.values():
        if items:
            queries.append(items[0])
        if len(items) >= 2:
            queries.append(f"{items[0]} and {items[1]}")
    return queries


def test_decomposer_queries_structure(demo_env, resolver_response, user_query) -> None:
    node = DecomposerNode(demo_env.ctx)
    state = GraphState(
        user_query=user_query,
        user_context=UserContext(roles=["admin"]),
        datasource_resolver_response=resolver_response,
    )
    result = node(state)

    assert "errors" not in result
    response = result["decomposer_response"]
    assert isinstance(response, DecomposerResponse)

    assert isinstance(response.sub_queries, list)
    assert isinstance(response.combine_groups, list)
    assert isinstance(response.post_combine_ops, list)
    assert isinstance(response.unmapped_subqueries, list)

    for sub_query in response.sub_queries:
        assert sub_query.id
        assert sub_query.datasource_id
        assert sub_query.intent

    subquery_ids = {sq.id for sq in response.sub_queries}
    for group in response.combine_groups:
        for inp in group.inputs:
            assert inp.subquery_id in subquery_ids

    group_ids = {group.group_id for group in response.combine_groups}
    for op in response.post_combine_ops:
        assert op.target_group_id in group_ids


def pytest_generate_tests(metafunc):
    if "user_query" in metafunc.fixturenames:
        root = _project_root()
        questions = _load_sample_questions(root)
        queries = _build_queries(questions)
        metafunc.parametrize("user_query", queries)
