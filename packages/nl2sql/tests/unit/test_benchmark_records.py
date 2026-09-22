"""Tier 2 result records and `publish`: what a record holds, and byte-stable pages built from them."""
import datetime as dt
import hashlib
import json
import pathlib
import random

from nl2sql.evaluation import records, tier2
from nl2sql.evaluation.gold import GOLD_DATASET_PATH

REPO = pathlib.Path(__file__).resolve().parents[4]
WHEN = dt.datetime(2026, 9, 21, 14, 5, 6, tzinfo=dt.timezone.utc)


def _rec(qid, status, pass_no=1):
    return {"id": qid, "question": "?", "role": "admin", "expected": "allowed", "status": status, "reason": "",
            "sql": "SELECT 1", "rows": 1, "gold_rows": 1, "pass": pass_no, "tags": ["join"], "difficulty": "easy",
            "cost": 0.02, "latency_s": 1.5, "rows_digest": "d", "error_codes": [], "refused_unanswerable": False,
            "retries": 0, "timings": {},
            "faithfulness": {"faithful": status == "pass", "unsupported_numbers": [] if status == "pass" else ["9"],
                             "unsupported_entities": [], "checked": 1},
            "tokens_by_node": {"ast_planner": {"calls": 1, "input_tokens": 1000, "cached_input_tokens": 400,
                                               "cache_write_input_tokens": 0, "output_tokens": 100,
                                               "reasoning_tokens": 20}}}


def _board(names=("gpt-5.4",), passes=1, stopped=None, statuses=("pass", "fail")):
    configs = {}
    for name in names:
        runs = [_rec(f"q{i}", s, p) for p in range(1, passes + 1) for i, s in enumerate(statuses)]
        board = tier2.score_config(runs, passes=passes)
        board.update(models={"astplanner": "openai:gpt-5.4", "decomposer": "openai:gpt-5.4-mini"},
                     planned_cases=len(runs), completed_cases=len(runs))
        configs[name] = board
    return {"tier": 2, "dataset": str(GOLD_DATASET_PATH), "roles": ["admin"], "passes": passes,
            "questions": ["q0", "q1"], "max_cost": 5.0, "spent": 0.1, "stopped": stopped,
            "prices_checked_on": "2026-09-21", "configs": configs, "comparison": tier2.compare(configs)}


def test_a_record_holds_when_what_on_which_data_and_the_headline_metrics():
    record = records.make_record(_board(passes=2), "gpt-5.4", recorded_at=WHEN, engine_version="0.9.0",
                                 git_commit="abc123")
    assert record["recorded_at"] == "2026-09-21T14:05:06Z"
    assert record["engine_version"] == "0.9.0" and record["git_commit"] == "abc123"
    assert record["dataset"] == {"name": "chinook_gold.yaml",
                                 "sha256": hashlib.sha256(GOLD_DATASET_PATH.read_bytes()).hexdigest()}
    assert record["config"] == {"name": "gpt-5.4", "models": {"astplanner": "openai:gpt-5.4",
                                                              "decomposer": "openai:gpt-5.4-mini"}}
    assert record["roles"] == ["admin"] and record["passes"] == 2
    m = record["metrics"]
    assert m["accuracy"] == 0.5 and m["cost_per_question"] == 0.02
    assert m["tokens_per_question"] == {"input": 1000, "cached": 400, "output": 100}
    assert m["latency_p50"] == 1.5 and m["determinism"] == 1.0
    assert m["faithfulness"] == 0.5
    assert record["stopped"] is None and record["partial"] is False
    assert record["scoreboard"]["accuracy"]["overall"] == 0.5


def test_a_stopped_run_is_recorded_as_partial():
    record = records.make_record(_board(stopped="max_cost"), "gpt-5.4", recorded_at=WHEN, engine_version="1",
                                 git_commit=None)
    assert record["stopped"] == "max_cost" and record["partial"] is True


def test_records_are_named_by_date_version_and_config_with_a_suffix_on_a_clash(tmp_path):
    board = _board(names=("gpt-5.4", "claude/planner"))
    first = records.write_records(board, tmp_path, recorded_at=WHEN, engine_version="0.9.0", git_commit=None)
    second = records.write_records(board, tmp_path, recorded_at=WHEN, engine_version="0.9.0", git_commit=None)
    assert [p.name for p in first] == ["2026-09-21_0.9.0_gpt-5.4.json", "2026-09-21_0.9.0_claude-planner.json"]
    assert [p.name for p in second] == ["2026-09-21_0.9.0_gpt-5.4-2.json", "2026-09-21_0.9.0_claude-planner-2.json"]
    assert json.loads(first[0].read_text(encoding="utf-8"))["config"]["name"] == "gpt-5.4"


def _write_some(tmp_path):
    for day, version, name, statuses in [(19, "0.8.0", "gpt-5.4", ("fail", "fail")),
                                         (20, "0.9.0", "gpt-5.4", ("pass", "fail")),
                                         (21, "0.9.0", "mini", ("pass", "pass"))]:
        when = WHEN.replace(day=day)
        records.write_records(_board(names=(name,), statuses=statuses), tmp_path, recorded_at=when,
                              engine_version=version, git_commit="abcdef1234")


def test_publish_is_byte_identical_whatever_order_the_records_are_read_in(tmp_path):
    _write_some(tmp_path)
    loaded = records.load_records(tmp_path)
    shuffled = list(loaded)
    random.Random(7).shuffle(shuffled)
    assert records.render_history(loaded) == records.render_history(shuffled)
    assert records.render_readme_block(loaded) == records.render_readme_block(shuffled)


def test_history_lists_runs_newest_first_and_the_latest_run_per_config(tmp_path):
    _write_some(tmp_path)
    page = records.render_history(records.load_records(tmp_path))
    runs = page.split("## All runs")[1]
    assert runs.index("2026-09-21") < runs.index("2026-09-20") < runs.index("2026-09-19")
    latest = page.split("## Latest per config")[1].split("## All runs")[0]
    assert "2026-09-20" in latest and "2026-09-19" not in latest  # gpt-5.4's newest only
    assert "50.0%" in latest and "100.0%" in latest
    assert "| Faithfulness |" in page.split("## All runs")[0] and "| Faithfulness |" in runs
    # A record written before faithfulness existed shows a dash.
    old = records.load_records(tmp_path)[0]
    del old["metrics"]["faithfulness"]
    assert records.render_history([old])
    block = records.render_readme_block(records.load_records(tmp_path))
    assert "docs/benchmarks.md" in block and "2026-09-19" not in block


def test_the_empty_state(tmp_path):
    assert records.EMPTY in records.render_history([])
    assert records.EMPTY in records.render_readme_block([])
    assert records.load_records(tmp_path / "missing") == []


def test_publish_replaces_only_the_readme_block(tmp_path):
    _write_some(tmp_path / "results")
    readme = tmp_path / "README.md"
    readme.write_text(f"# Title\n\nbefore\n{records.START}\nold\n{records.END}\nafter\n", encoding="utf-8")
    history = tmp_path / "docs" / "benchmarks.md"
    records.publish(tmp_path / "results", history, readme)
    text = readme.read_text(encoding="utf-8")
    assert text.startswith("# Title\n\nbefore\n") and text.endswith("after\n") and "old" not in text
    assert "mini" in text and history.read_text(encoding="utf-8").startswith("# Benchmark Results")


def test_the_committed_pages_match_what_publish_generates_from_the_committed_records():
    committed = records.load_records(REPO / records.RESULTS_DIR)
    history = (REPO / records.HISTORY_PATH).read_text(encoding="utf-8")
    readme = (REPO / records.README_PATH).read_text(encoding="utf-8")
    assert history == records.render_history(committed), "run `nl2sql benchmark publish` and commit the result"
    assert readme == records.replace_block(readme, records.render_readme_block(committed)), \
        "run `nl2sql benchmark publish` and commit the result"
