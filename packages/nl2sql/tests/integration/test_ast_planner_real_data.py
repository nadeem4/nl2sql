from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nl2sql.runtime import Runtime
from nl2sql.testing.config import make_demo_config
from nl2sql.pipeline.nodes.ast_planner.node import ASTPlannerNode
from nl2sql.pipeline.nodes.decomposer.schemas import ExpectedColumn, SubQuery
from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode
from nl2sql.pipeline.state import SubgraphExecutionState


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

    tmp_dir = Path(tempfile.mkdtemp(prefix="ast_planner_real_data_"))

    monkeypatch = pytest.MonkeyPatch()
    collection_name = f"itest_ast_planner_{uuid.uuid4().hex}"
    vector_store_path = tmp_dir / "chroma"
    schema_store_path = tmp_dir / "schema_store.db"

    ctx = Runtime.from_config(make_demo_config(tmp_dir))

    env = SimpleNamespace(ctx=ctx, root=root, tmp_dir=tmp_dir)
    try:
        yield env
    finally:
        monkeypatch.undo()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _build_queries(questions: dict[str, list[str]]) -> list[tuple[str, str]]:
    queries: list[tuple[str, str]] = []
    for ds_id, items in questions.items():
        if items:
            queries.append((ds_id, items[0]))
        if len(items) >= 2:
            queries.append((ds_id, f"{items[0]} and {items[1]}"))
    return queries


def pytest_generate_tests(metafunc):
    if "datasource_id" in metafunc.fixturenames and "user_query" in metafunc.fixturenames:
        root = _project_root()
        questions = _load_sample_questions(root)
        queries = _build_queries(questions)
        metafunc.parametrize(("datasource_id", "user_query"), queries)


def test_ast_planner_real_data(demo_env, datasource_id, user_query) -> None:
    schema_node = SchemaRetrieverNode(demo_env.ctx)
    sub_query = SubQuery(
        id="sq1",
        datasource_id=datasource_id,
        intent=user_query,
        expected_schema=[ExpectedColumn(name="value", dtype="string")],
    )
    subgraph_state = SubgraphExecutionState(
        trace_id="t",
        sub_query=sub_query,
    )
    schema_result = schema_node(subgraph_state)
    subgraph_state.relevant_tables = schema_result["relevant_tables"]

    node = ASTPlannerNode(demo_env.ctx)
    result = node(subgraph_state)

    assert "errors" in result
    assert result["errors"] == []
    response = result["ast_planner_response"]
    plan = response.plan
    assert plan is not None
    assert plan.query_type == "READ"
    assert isinstance(plan.tables, list)
    assert isinstance(plan.select_items, list)
    for table in plan.tables:
        assert table.name
        assert table.alias
    for item in plan.select_items:
        assert item.expr is not None
