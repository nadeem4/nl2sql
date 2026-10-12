"""A result fetched in many batches is whole, and the combined result is saved, end to end.

"List every genre" returns Chinook's 25 genres. With ``RESULT_FETCH_BATCH_SIZE=4``
the SQLite adapter yields them in seven batches, which the executor writes to
the sub-query's artifact one at a time. The aggregator then saves the combined
result as its own artifact in the run's folder, and the answer model's preview
is read from it: the first 20 rows and the count of all 25.
"""
from __future__ import annotations

import json
import re
import sqlite3

import polars as pl
import pytest

from .conftest import _base_env, run_cli
from .test_answer_preview_fake_llm import QUESTION, RULES
from .test_trace_fake_llm import _serve


@pytest.mark.e2e
def test_a_multi_batch_result_is_whole_and_its_combined_result_is_saved(demo_project, tmp_path):
    artifacts, traces = tmp_path / "artifacts", tmp_path / "traces"
    server = _serve(demo_project, RULES, "llm.batched-fetch.yaml")
    env = _base_env()
    env.update({"OPENAI_API_KEY": "sk-" + "fake-batched-fetch", "TRACE_MODE": "always", "TRACE_DIR": str(traces),
                "RESULT_FETCH_BATCH_SIZE": "4", "RESULT_ARTIFACT_BASE_URI": str(artifacts),
                "SQL_AGENT_RETRY_BASE_DELAY_SEC": "0", "SQL_AGENT_RETRY_JITTER_SEC": "0"})
    try:
        r = run_cli(demo_project, env, "run", "--llm-config", "configs/llm.batched-fetch.yaml", QUESTION)
    finally:
        server.stop()

    assert r.returncode == 0, r.stdout + r.stderr
    [trace] = list(traces.glob("*.json"))
    result = json.loads(trace.read_text(encoding="utf-8"))["result"]
    assert result["status"] == "success", result["errors"]
    [db] = list(demo_project.rglob("chinook.sqlite"))
    with sqlite3.connect(db) as conn:
        genres = [name for (name,) in conn.execute("SELECT Name FROM Genre ORDER BY GenreId")]
    assert len(genres) == 25

    [sub] = result["sub_queries"]
    assert sub["rows"]["total_rows"] == 25
    assert [row[0] for row in sub["rows"]["rows"]] == genres

    [run_folder] = [p for p in artifacts.rglob("*") if p.is_dir() and any(p.glob("*.parquet"))]
    # The engine renames the decomposer's sub-query ids (sq_<hash>), and the combine after them.
    [combined] = list(run_folder.glob("result_combine_*.parquet"))
    [scan] = [p for p in run_folder.glob("*.parquet") if p != combined]
    assert pl.read_parquet(scan)["genre_name"].to_list() == genres
    assert pl.read_parquet(combined)["genre_name"].to_list() == genres

    [synthesis] = [c for c in server.calls if c["name"] == "AggregatedResponse"]
    prompt = "\n".join(str(m.get("content") or "") for m in synthesis["body"].get("messages", []))
    assert len(re.findall(r'"genre_name":', prompt)) == 20
    assert '"row_count": 25' in prompt and '"partial": true' in prompt
