from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nl2sql.policies import UserContext
from nl2sql.common.errors import ErrorSeverity
from nl2sql.runtime import Runtime
from nl2sql.testing.config import make_demo_config
from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.pipeline.nodes.ast_planner.node import ASTPlannerNode
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.pipeline.nodes.schema_retriever.schema import Table, Column


# The node under test builds an LLM client in its constructor, so these cannot
# run without a real key. See docs/testing/architecture.md.
pytestmark = pytest.mark.llm


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _load_sample_questions(root: Path) -> dict[str, list[str]]:
    """The demo's guided questions, by datasource (``nl2sql.datasets.demo``)."""
    from nl2sql.datasets.demo import DEMO_QUESTIONS_BY_DATASOURCE

    return {ds: list(questions) for ds, questions in DEMO_QUESTIONS_BY_DATASOURCE.items()}


def _tables_from_snapshot(snapshot) -> list[Table]:
    tables: list[Table] = []
    for table_contract in snapshot.contract.tables.values():
        columns = [
            Column(name=col.name, type=col.data_type)
            for col in table_contract.columns.values()
        ]
        tables.append(Table(name=table_contract.table.table_name, columns=columns))
    return tables


@pytest.fixture(scope="module")
def demo_env() -> SimpleNamespace:
    root = _project_root()

    tmp_dir = Path(tempfile.mkdtemp(prefix="logical_validator_real_data_"))

    monkeypatch = pytest.MonkeyPatch()
    collection_name = f"itest_logical_validator_{uuid.uuid4().hex}"
    vector_store_path = tmp_dir / "chroma"
    schema_store_path = tmp_dir / "schema_store.db"

    ctx = Runtime.from_config(make_demo_config(tmp_dir))

    orchestrator = IndexingOrchestrator(ctx)
    for adapter in ctx.ds_registry.list_adapters():
        orchestrator.index_datasource(adapter)

    env = SimpleNamespace(ctx=ctx, root=root, tmp_dir=tmp_dir)
    try:
        yield env
    finally:
        monkeypatch.undo()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def pytest_generate_tests(metafunc):
    if "datasource_id" in metafunc.fixturenames and "user_query" in metafunc.fixturenames:
        root = _project_root()
        questions = _load_sample_questions(root)
        cases = []
        for ds_id, items in questions.items():
            if items:
                cases.append((ds_id, items[0]))
        metafunc.parametrize(("datasource_id", "user_query"), cases)


def test_logical_validator_real_data(demo_env, datasource_id, user_query) -> None:
    planner_node = ASTPlannerNode(demo_env.ctx)
    validator_node = LogicalValidatorNode(demo_env.ctx)

    sub_query = SubQuery(
        id="sq1",
        datasource_id=datasource_id,
        intent=user_query,
    )
    subgraph_state = SubgraphExecutionState(
        trace_id="t",
        sub_query=sub_query,
        user_context=UserContext(roles=["admin"]),
    )

    snapshot = demo_env.ctx.schema_store.get_latest_snapshot(datasource_id)
    subgraph_state.relevant_tables = _tables_from_snapshot(snapshot)

    planner_result = planner_node(subgraph_state)
    assert planner_result["errors"] == []
    subgraph_state.ast_planner_response = planner_result["ast_planner_response"]

    result = validator_node(subgraph_state)
    errors = result["logical_validator_response"].errors
    assert all(
        e.severity not in (ErrorSeverity.CRITICAL, ErrorSeverity.ERROR)
        for e in errors
    )
