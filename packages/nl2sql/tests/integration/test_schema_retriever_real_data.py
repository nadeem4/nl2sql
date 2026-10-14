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
from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode
from nl2sql.pipeline.state import SubgraphExecutionState


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _load_sample_questions(root: Path) -> dict[str, list[str]]:
    """The demo's guided questions, by datasource (``nl2sql.datasets.demo``)."""
    from nl2sql.datasets.demo import DEMO_QUESTIONS_BY_DATASOURCE

    return {ds: list(questions) for ds, questions in DEMO_QUESTIONS_BY_DATASOURCE.items()}


@pytest.fixture(scope="module")
def indexed_env() -> SimpleNamespace:
    root = _project_root()

    tmp_dir = Path(tempfile.mkdtemp(prefix="schema_retriever_real_data_"))

    monkeypatch = pytest.MonkeyPatch()
    collection_name = f"itest_schema_retriever_{uuid.uuid4().hex}"
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
        # Chinook is a single datasource, so take several of its questions
        # rather than one per datasource: otherwise this shrinks to one case.
        cases = []
        for ds_id, items in questions.items():
            for question in (items or [])[:4]:
                cases.append((ds_id, question))
        metafunc.parametrize(("datasource_id", "user_query"), cases)


def test_schema_retriever_real_data(indexed_env, datasource_id, user_query) -> None:
    node = SchemaRetrieverNode(indexed_env.ctx)
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id=datasource_id, intent=user_query),
    )

    result = node(state)

    tables = result["relevant_tables"]
    assert tables
    assert all(t.name for t in tables)
