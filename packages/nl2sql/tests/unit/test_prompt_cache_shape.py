"""The planner, refiner and decomposer prompts are slim and cache-friendly.

Providers cache a stable prompt prefix (OpenAI: 1,024+ tokens, automatically).
So the stable context (instructions, examples, schema) sits in the system
message and everything that changes per question sits in the last human
message, and the schema renders byte-identically for the same snapshot and role.
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table, render_schema_for_prompt

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "measure_prompt_tokens.py"
Q1 = "How many customers do we have, by country?"
Q2 = "Which artist has the most albums?"


def _load_script():
    spec = importlib.util.spec_from_file_location("measure_prompt_tokens", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def measure():
    return _load_script()


@pytest.fixture(scope="module")
def tables(measure):
    return measure.chinook_tables()


def _stats():
    return {"null_percentage": 0.0, "distinct_count": 24, "min_value": "Alternative",
            "max_value": "World", "sample_values": ["Rock", "Jazz"]}


def test_schema_render_is_compact_and_keeps_only_sample_values():
    table = Table(
        name="Genre",
        columns=[Column(name="Name", type="NVARCHAR(120)", stats=_stats(), description=None),
                 Column(name="GenreId", type="INTEGER", stats={}, description="")],
        description="",
        primary_key=["GenreId"],
        schema_version="20260920T000000_0123abcd",
        relationships=[],
    )

    text = render_schema_for_prompt([table])

    assert "\n" not in text  # one line per table, no indentation
    row = json.loads(text)
    assert text == json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    for dropped in ("null_percentage", "distinct_count", "min_value", "max_value",
                    "schema_version", "stats", "relationships", "description"):
        assert dropped not in text, dropped
    assert ":null" not in text and '""' not in text and "[]" not in text and "{}" not in text
    assert row["columns"][0] == {"name": "Name", "type": "NVARCHAR(120)", "sample_values": ["Rock", "Jazz"]}
    assert row["columns"][1] == {"name": "GenreId", "type": "INTEGER"}
    assert row["primary_key"] == ["GenreId"]


def test_schema_render_does_not_depend_on_table_or_key_order():
    a = Table(name="A", columns=[Column(name="x", type="INT", stats={"sample_values": [1]})])
    b = Table(name="B", columns=[Column(name="y", type="INT")], description="bee")
    b_reordered = Table.model_validate({"description": "bee", "columns": [{"type": "INT", "name": "y"}], "name": "B"})

    assert render_schema_for_prompt([a, b]) == render_schema_for_prompt([b_reordered, a])


def test_a_table_the_role_cannot_read_sends_no_sample_values(measure):
    tables = measure.chinook_tables(readable=lambda ref: ref.table_name != "Customer")
    rows = {json.loads(line)["name"]: json.loads(line) for line in render_schema_for_prompt(tables).splitlines()}

    assert not any("sample_values" in c for c in rows["Customer"]["columns"])
    assert any("sample_values" in c for c in rows["Genre"]["columns"])


def test_planner_keeps_stable_context_in_system_and_the_question_last(measure, tables):
    m1, _ = measure.render_planner(Q1, tables)
    m2, _ = measure.render_planner(Q2, tables)

    assert [m.type for m in m1] == ["system", "human"]
    assert m1[0].content == m2[0].content  # the cacheable part is identical
    assert Q1 not in m1[0].content and Q1 in m1[1].content
    assert render_schema_for_prompt(tables) in m1[0].content
    assert "min_value" not in m1[0].content


def test_planner_prompts_for_two_questions_share_a_prefix_of_at_least_1024_tokens(measure, tables):
    count, unit = measure.token_counter()
    t1 = measure.as_text(measure.render_planner(Q1, tables)[0])
    t2 = measure.as_text(measure.render_planner(Q2, tables)[0])

    prefix = measure.common_prefix([t1, t2])

    # ~4 characters per token when tiktoken is not available.
    assert count(prefix) >= (1024 if unit == "tokens" else 4096)


def test_refiner_keeps_stable_context_in_system_and_the_question_last(measure, tables):
    m1, _ = measure.render_refiner(Q1, tables)
    m2, _ = measure.render_refiner(Q2, tables)

    assert [m.type for m in m1] == ["system", "human"]
    assert m1[0].content == m2[0].content
    assert render_schema_for_prompt(tables) in m1[0].content
    assert Q1 in m1[1].content and measure.RETRY_ERROR in m1[1].content


def test_decomposer_keeps_its_instructions_in_system_and_the_question_last(measure):
    m1, _ = measure.render_decomposer(Q1)
    m2, _ = measure.render_decomposer(Q2)

    assert [m.type for m in m1] == ["system", "human"]
    assert m1[0].content == m2[0].content
    assert Q1 in m1[1].content


def _planner_system_digest(seed: str) -> str:
    code = (
        "import hashlib, importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('m', r'{SCRIPT}')\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        f"msgs, _ = m.render_planner({Q1!r}, m.chinook_tables())\n"
        "print(hashlib.sha256(msgs[0].content.encode('utf-8')).hexdigest())\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": seed}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    return out.stdout.strip().splitlines()[-1]


def test_planner_system_prompt_is_byte_identical_across_processes(measure, tables):
    here = hashlib.sha256(measure.render_planner(Q1, tables)[0][0].content.encode("utf-8")).hexdigest()

    assert _planner_system_digest("1") == _planner_system_digest("2") == here
