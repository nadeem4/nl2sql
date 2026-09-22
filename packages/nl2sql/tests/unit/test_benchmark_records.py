"""Benchmark result records and `publish`: what a record holds, where it lives, comparable runs and stable pages."""
import datetime as dt
import hashlib
import json
import pathlib
import random
import subprocess
from types import SimpleNamespace

import pytest

from nl2sql.evaluation import records, tier2
from nl2sql.evaluation.gold import GOLD_DATASET_PATH

REPO = pathlib.Path(__file__).resolve().parents[4]
WHEN = dt.datetime(2026, 9, 21, 14, 5, 6, tzinfo=dt.timezone.utc)
CODE = {"git_commit": "abcdef1234567890", "git_dirty": False}
CHINOOK = {"datasource_id": "chinook", "engine": "sqlite", "schema_fingerprint": "ceed80fefde051bd",
           "tables": 11, "columns": 64}


def _rec(qid, status, pass_no=1, cost=0.02):
    return {"id": qid, "question": "?", "role": "admin", "expected": "allowed", "status": status, "reason": "",
            "sql": "SELECT 1", "rows": 1, "gold_rows": 1, "pass": pass_no, "tags": ["join"], "difficulty": "easy",
            "cost": cost, "latency_s": 1.5, "rows_digest": "d", "error_codes": [], "refused_unanswerable": False,
            "retries": 0, "timings": {},
            "faithfulness": {"faithful": status == "pass", "unsupported_numbers": [] if status == "pass" else ["9"],
                             "unsupported_entities": [], "checked": 1},
            "tokens_by_node": {"ast_planner": {"calls": 1, "input_tokens": 1000, "cached_input_tokens": 400,
                                               "cache_write_input_tokens": 0, "output_tokens": 100,
                                               "reasoning_tokens": 20}}}


def _board(names=("gpt-5.4",), passes=1, stopped=None, statuses=("pass", "fail"), database=CHINOOK,
           roles=("admin",), cost=0.02):
    configs = {}
    for name in names:
        runs = [_rec(f"q{i}", s, p, cost) for p in range(1, passes + 1) for i, s in enumerate(statuses)]
        board = tier2.score_config(runs, passes=passes)
        board.update(models={"astplanner": "openai:gpt-5.4", "decomposer": "openai:gpt-5.4-mini"},
                     planned_cases=len(runs), completed_cases=len(runs))
        configs[name] = board
    return {"tier": 2, "dataset": str(GOLD_DATASET_PATH), "roles": list(roles), "passes": passes,
            "questions": ["q0", "q1"], "max_cost": 5.0, "spent": 0.1, "stopped": stopped,
            "prices_checked_on": "2026-09-21", "configs": configs, "comparison": tier2.compare(configs),
            "database": database}


def _retrieval_report(table=0.9, column=0.7, database=CHINOOK):
    summary = {"questions": 3, "table_recall": table, "column_recall": column, "perfect_tables": 2,
               "perfect_columns": 1, "tables_sent": 8.0, "columns_sent": 24.0}
    return {"kind": "retrieval", "dataset": str(GOLD_DATASET_PATH), "database": database,
            "settings": {"table_k": 8, "planning_k": 12, "embedding": "local"}, "summary": summary, "results": []}


def test_a_record_holds_when_what_on_which_code_data_and_database_and_the_headline_metrics():
    record = records.make_record(_board(passes=2), "gpt-5.4", recorded_at=WHEN, engine_version="0.9.0",
                                 code=CODE, note="slim prompts")
    assert record["schema"] == 2 and record["kind"] == "tier2"
    assert record["recorded_at"] == "2026-09-21T14:05:06Z"
    assert record["engine_version"] == "0.9.0"
    assert (record["git_commit"], record["git_dirty"]) == ("abcdef1234567890", False)
    assert record["note"] == "slim prompts"
    assert record["database"] == CHINOOK
    assert record["dataset"] == {"name": "chinook_gold.yaml",
                                 "sha256": hashlib.sha256(GOLD_DATASET_PATH.read_bytes().replace(b"\r\n", b"\n"))
                                 .hexdigest()}
    assert record["config"] == {"name": "gpt-5.4", "models": {"astplanner": "openai:gpt-5.4",
                                                              "decomposer": "openai:gpt-5.4-mini"}}
    assert record["roles"] == ["admin"] and record["passes"] == 2
    m = record["metrics"]
    assert m["accuracy"] == 0.5 and m["lenient_accuracy"] == 0.5 and m["cost_per_question"] == 0.02
    assert m["tokens_per_question"] == {"input": 1000, "cached": 400, "output": 100}
    assert m["latency_p50"] == 1.5 and m["determinism"] == 1.0
    assert m["faithfulness"] == 0.5
    assert record["stopped"] is None and record["partial"] is False
    assert record["scoreboard"]["accuracy"]["overall"] == 0.5


def test_without_a_known_code_or_database_the_record_says_unknown_not_null():
    board = _board()
    del board["database"]
    record = records.make_record(board, "gpt-5.4", recorded_at=WHEN, engine_version="1", code={})
    assert record["git_commit"] == "unknown" and record["git_dirty"] == "unknown"
    assert record["database"]["datasource_id"] == "unknown" and record["note"] is None


def test_the_dataset_hash_ignores_line_endings(tmp_path):
    lf, crlf = tmp_path / "lf.yaml", tmp_path / "crlf.yaml"
    lf.write_bytes(b"- id: a\n- id: b\n")
    crlf.write_bytes(b"- id: a\r\n- id: b\r\n")
    assert records.dataset_id(lf)["sha256"] == records.dataset_id(crlf)["sha256"]


def test_a_stopped_run_is_recorded_as_partial():
    record = records.make_record(_board(stopped="max_cost"), "gpt-5.4", recorded_at=WHEN, engine_version="1",
                                 code=CODE)
    assert record["stopped"] == "max_cost" and record["partial"] is True


def test_records_live_under_kind_database_date_sha_config_and_are_never_overwritten(tmp_path):
    board = _board(names=("gpt-5.4", "claude/planner"))
    first = records.write_records(board, tmp_path, recorded_at=WHEN, engine_version="0.9.0", code=CODE)
    content = first[0].read_bytes()
    second = records.write_records(board, tmp_path, recorded_at=WHEN, engine_version="0.9.0", code=CODE, note="x")
    rel = [p.relative_to(tmp_path).as_posix() for p in first + second]
    assert rel == ["tier2/chinook/2026-09-21_abcdef1_gpt-5.4.json",
                   "tier2/chinook/2026-09-21_abcdef1_claude-planner.json",
                   "tier2/chinook/2026-09-21_abcdef1_gpt-5.4-2.json",
                   "tier2/chinook/2026-09-21_abcdef1_claude-planner-2.json"]
    assert first[0].read_bytes() == content
    assert json.loads(first[0].read_text(encoding="utf-8"))["config"]["name"] == "gpt-5.4"
    path = records.write_retrieval_record(_retrieval_report(), tmp_path, recorded_at=WHEN, engine_version="0.9.0",
                                          code={"git_commit": "unknown", "git_dirty": "unknown"}, note="baseline")
    assert path.relative_to(tmp_path).as_posix() == "retrieval/chinook/2026-09-21_unknown_retrieval.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["database"] == CHINOOK and body["note"] == "baseline" and "database" not in body["report"]


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_the_git_commit_comes_from_the_package_checkout_not_the_current_folder(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    package = repo / "src" / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    elsewhere = tmp_path / "demo"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # a project folder outside any checkout

    assert records.code_version(package / "__init__.py") == {"git_commit": sha, "git_dirty": False}
    (repo / "untracked.txt").write_text("u", encoding="utf-8")
    assert records.code_version(package / "__init__.py")["git_dirty"] is False
    (package / "__init__.py").write_text("x = 2\n", encoding="utf-8")
    assert records.code_version(package / "__init__.py") == {"git_commit": sha, "git_dirty": True}
    assert records.code_version(elsewhere) == {"git_commit": "unknown", "git_dirty": "unknown"}
    # The installed engine is this repo's editable checkout.
    assert len(records.code_version()["git_commit"]) == 40


def test_database_identity_fingerprints_the_latest_schema_snapshot():
    columns = {"a": object(), "b": object()}
    contract = SimpleNamespace(datasource_id="chinook", engine_type="sqlite",
                               tables={"t1": SimpleNamespace(columns=columns, foreign_keys=[]),
                                       "t2": SimpleNamespace(columns={"c": object()}, foreign_keys=[])})
    snapshots = {"chinook": SimpleNamespace(contract=contract)}
    ctx = SimpleNamespace(ds_registry=SimpleNamespace(list_ids=lambda: ["chinook"]),
                          schema_store=SimpleNamespace(get_latest_snapshot=snapshots.get))
    import nl2sql.schema.protocol as protocol
    original = protocol.generate_schema_fingerprint
    protocol.generate_schema_fingerprint = lambda c: "f" * 64
    try:
        assert records.database_identity(ctx) == {"datasource_id": "chinook", "engine": "sqlite",
                                                  "schema_fingerprint": "f" * 16, "tables": 2, "columns": 3}
    finally:
        protocol.generate_schema_fingerprint = original
    empty = SimpleNamespace(ds_registry=SimpleNamespace(list_ids=lambda: ["x"]),
                            schema_store=SimpleNamespace(get_latest_snapshot=lambda _id: None))
    assert records.database_identity(empty)["schema_fingerprint"] == "unknown"


def _write(root, day, name="gpt-5.4", statuses=("pass", "fail"), database=CHINOOK, roles=("admin",), cost=0.02,
           note=None):
    return records.write_records(_board(names=(name,), statuses=statuses, database=database, roles=roles, cost=cost),
                                 root, recorded_at=WHEN.replace(day=day), engine_version="0.9.0", code=CODE,
                                 note=note)


def test_runs_are_comparable_on_kind_dataset_schema_and_roles():
    a = records.make_record(_board(), "gpt-5.4", recorded_at=WHEN, engine_version="1", code=CODE)
    assert records.comparable(a, dict(a))
    other_schema = {**a, "database": {**CHINOOK, "schema_fingerprint": "0" * 16}}
    other_data = {**a, "dataset": {"name": "x.yaml", "sha256": "1" * 64}}
    other_roles = {**a, "roles": ["sales"]}
    retrieval = {**a, "kind": "retrieval"}
    assert not any(records.comparable(a, b) for b in (other_schema, other_data, other_roles, retrieval))


def test_the_change_against_the_previous_run_of_the_same_config(tmp_path):
    _write(tmp_path, 19, statuses=("fail", "fail"), cost=0.03)
    _write(tmp_path, 20, statuses=("pass", "fail"), cost=0.02, note="slim prompts")
    _write(tmp_path, 21, name="mini", statuses=("pass", "pass"))
    recs = records.load_records(tmp_path)
    newest_gpt = next(r for r in recs if r["config"]["name"] == "gpt-5.4")
    previous = records.previous_runs(recs)
    old = previous[id(newest_gpt)]
    assert old["recorded_at"].startswith("2026-09-19")
    assert records.deltas(newest_gpt, old) == {"accuracy": 0.5, "lenient_accuracy": 0.5, "faithfulness": 0.5,
                                               "cost_per_question": -0.01}
    assert records.describe_change(newest_gpt, old) == ("accuracy +50.0 pp, lenient +50.0 pp, "
                                                        "faithfulness +50.0 pp, $/question -$0.0100")
    mini = next(r for r in recs if r["config"]["name"] == "mini")
    assert previous[id(mini)] is None and records.describe_change(mini, None) == "first run"


def test_a_changed_schema_or_dataset_starts_a_new_series(tmp_path):
    _write(tmp_path, 19)
    _write(tmp_path, 20, database={**CHINOOK, "schema_fingerprint": "0123456789abcdef"})
    _write(tmp_path, 21, database={**CHINOOK, "schema_fingerprint": "0123456789abcdef"}, roles=("sales",))
    recs = records.load_records(tmp_path)
    previous = records.previous_runs(recs)
    by_day = {r["recorded_at"][8:10]: r for r in recs}
    assert records.describe_change(by_day["20"], previous[id(by_day["20"])]) == "new series (schema changed)"
    assert records.describe_change(by_day["21"], previous[id(by_day["21"])]) == "new series (roles changed)"
    page = records.render_history(recs)
    assert page.split("### All runs")[1].count("new series") == 2


def test_history_groups_by_benchmark_then_database_newest_first(tmp_path):
    _write(tmp_path, 19, statuses=("fail", "fail"))
    _write(tmp_path, 20, note="slim prompts")
    _write(tmp_path, 21, name="mini", statuses=("pass", "pass"))
    _write(tmp_path, 21, database={**CHINOOK, "datasource_id": "northwind", "schema_fingerprint": "9" * 16})
    for day, table in ((19, 0.9), (20, 0.95)):
        records.write_retrieval_record(_retrieval_report(table=table), tmp_path, recorded_at=WHEN.replace(day=day),
                                       engine_version="0.9.0", code=CODE)
    page = records.render_history(records.load_records(tmp_path))
    headings = [line for line in page.splitlines() if line.startswith("## ")]
    assert headings == ["## Tier 2: chinook (sqlite, 11 tables / 64 columns)",
                        "## Tier 2: northwind (sqlite, 11 tables / 64 columns)",
                        "## Retrieval recall: chinook (sqlite, 11 tables / 64 columns)"]
    chinook = page.split(headings[0])[1].split(headings[1])[0]
    runs = chinook.split("### All runs")[1]
    assert runs.index("2026-09-21") < runs.index("2026-09-20") < runs.index("2026-09-19")
    latest = chinook.split("### Latest per config")[1].split("### All runs")[0]
    assert "2026-09-20" in latest and "2026-09-19" not in latest  # gpt-5.4's newest only
    assert "slim prompts" in latest and "abcdef1" in latest
    retrieval = page.split(headings[2])[1]
    assert "tables +5.0 pp, columns +0.0 pp" in retrieval and "first run" in retrieval
    block = records.render_readme_block(records.load_records(tmp_path))
    assert "docs/benchmarks.md" in block and "2026-09-19" not in block
    tier2_part, retrieval_part = block.split(records.README_RETRIEVAL_HEADING)
    assert records.README_TIER2_HEADING in tier2_part
    assert "| gpt-5.4 | chinook | 2026-09-20 |" in tier2_part and "| gpt-5.4 | northwind | 2026-09-21 |" in tier2_part
    assert "| mini | chinook | 2026-09-21 |" in tier2_part and "table_recall" not in tier2_part
    assert "| chinook | 2026-09-20 | abcdef1 | 95.0% | 70.0% |" in retrieval_part


def test_publish_is_byte_identical_whatever_order_the_records_are_read_in(tmp_path):
    _write(tmp_path, 19)
    _write(tmp_path, 20)
    _write(tmp_path, 21, name="mini")
    loaded = records.load_records(tmp_path)
    shuffled = list(loaded)
    random.Random(7).shuffle(shuffled)
    assert records.render_history(loaded) == records.render_history(shuffled)
    assert records.render_readme_block(loaded) == records.render_readme_block(shuffled)


def test_a_record_without_faithfulness_shows_a_dash(tmp_path):
    _write(tmp_path, 19)
    old = records.load_records(tmp_path)[0]
    del old["metrics"]["faithfulness"]
    assert "| - |" in records.render_history([old])


def test_the_empty_state(tmp_path):
    assert records.EMPTY in records.render_history([])
    block = records.render_readme_block([])
    assert records.TIER2_EMPTY in block and records.RETRIEVAL_EMPTY in block
    assert block.index(records.README_TIER2_HEADING) < block.index(records.README_RETRIEVAL_HEADING)
    assert records.load_records(tmp_path / "missing") == []


def test_the_readme_block_says_when_one_benchmark_has_no_run_yet(tmp_path):
    records.write_retrieval_record(_retrieval_report(), tmp_path, recorded_at=WHEN, engine_version="0.9.0", code=CODE)
    block = records.render_readme_block(records.load_records(tmp_path))
    assert records.TIER2_EMPTY in block and records.RETRIEVAL_EMPTY not in block
    assert "| chinook | 2026-09-21 | abcdef1 | 90.0% | 70.0% | first run |" in block


def test_publish_replaces_only_the_readme_block_and_never_touches_a_record(tmp_path):
    paths = _write(tmp_path / "benchmarks", 19) + _write(tmp_path / "benchmarks", 20, name="mini")
    before = {p: p.read_bytes() for p in paths}
    readme = tmp_path / "README.md"
    readme.write_text(f"# Title\n\nbefore\n{records.START}\nold\n{records.END}\nafter\n", encoding="utf-8")
    history = tmp_path / "docs" / "benchmarks.md"
    assert records.publish(tmp_path / "benchmarks", history, readme) == 2
    text = readme.read_text(encoding="utf-8")
    assert text.startswith("# Title\n\nbefore\n") and text.endswith("after\n") and "\nold\n" not in text
    assert "mini" in text and history.read_text(encoding="utf-8").startswith("# Benchmark Results")
    assert {p: p.read_bytes() for p in paths} == before


def test_import_copies_new_records_skips_identical_ones_and_reports_conflicts(tmp_path):
    demo, repo = tmp_path / "demo", tmp_path / "repo" / "benchmarks"
    [new] = _write(demo / "benchmarks", 19)
    [same] = _write(demo / "benchmarks", 20)
    [clash] = _write(demo / "benchmarks", 21)
    (repo / "tier2" / "chinook").mkdir(parents=True)
    (repo / same.relative_to(demo / "benchmarks")).write_bytes(same.read_bytes())
    mine = repo / clash.relative_to(demo / "benchmarks")
    mine.write_text('{"mine": true}\n', encoding="utf-8")
    legacy = demo / "benchmarks" / "retrieval" / "2026-09-22_0.1.2_retrieval.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"schema": 1, "kind": "retrieval"}), encoding="utf-8")

    outcome = records.import_records([demo], repo)
    rel = new.relative_to(demo / "benchmarks").as_posix()
    assert outcome["copied"] == [rel]
    assert outcome["identical"] == [same.relative_to(demo / "benchmarks").as_posix()]
    assert outcome["conflicts"] == [clash.relative_to(demo / "benchmarks").as_posix()]
    assert outcome["old_format"] == [str(legacy)]
    assert (repo / rel).read_bytes() == new.read_bytes()
    assert mine.read_text(encoding="utf-8") == '{"mine": true}\n'  # never overwritten
    again = records.import_records([demo], repo)
    assert again["copied"] == [] and len(again["identical"]) == 2


def test_every_committed_record_is_in_the_current_layout_and_where_its_fields_say():
    committed = records.load_records(REPO / records.BENCHMARKS_DIR)
    assert committed, "the first retrieval baseline is committed"
    for r in committed:
        assert r["schema"] == records.SCHEMA_VERSION
        assert r["git_commit"] and r["git_commit"] != "unknown" and r["git_dirty"] in (True, False, "unknown")
        assert set(r["database"]) >= {"datasource_id", "engine", "schema_fingerprint", "tables", "columns"}
        expected = records.record_relpath(r)
        assert r["_file"].rsplit("/", 1)[0] == expected.parent.as_posix()
        assert r["_file"].rsplit("/", 1)[1].startswith(expected.stem)


def test_the_migrated_retrieval_baseline():
    path = REPO / "benchmarks" / "retrieval" / "chinook" / "2026-09-22_571cd16_retrieval.json"
    r = json.loads(path.read_text(encoding="utf-8"))
    assert r["git_commit"].startswith("571cd16") and r["note"] == "first retrieval baseline"
    assert r["database"] == CHINOOK
    assert r["metrics"]["table_recall"] == pytest.approx(0.9846)


def test_the_committed_pages_match_what_publish_generates_from_the_committed_records():
    committed = records.load_records(REPO / records.BENCHMARKS_DIR)
    history = (REPO / records.HISTORY_PATH).read_text(encoding="utf-8")
    readme = (REPO / records.README_PATH).read_text(encoding="utf-8")
    assert history == records.render_history(committed), "run `nl2sql benchmark publish` and commit the result"
    assert readme == records.replace_block(readme, records.render_readme_block(committed)), \
        "run `nl2sql benchmark publish` and commit the result"
