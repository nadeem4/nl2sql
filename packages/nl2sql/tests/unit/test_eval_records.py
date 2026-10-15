"""Eval records and `publish`: what a record holds, where it lives, comparable runs and stable pages.

A record is one config's board of an ``EvalReport``, keyed by the suite's
digest and the run's mode. The records 0.6's ``nl2sql benchmark`` wrote
(kind ``tier2``) stay readable: they are mode full runs, and the history
shows them in the same series table.
"""
import datetime as dt
import json
import pathlib
import random
import subprocess
from types import SimpleNamespace

import pytest

from nl2sql.evaluation import records, report
from nl2sql.evaluation.report import EvalReport
from nl2sql.evaluation.suite import CHINOOK_SUITE_PATH, ROUTING_SUITE_PATH, load_suite

REPO = pathlib.Path(__file__).resolve().parents[4]
WHEN = dt.datetime(2026, 9, 21, 14, 5, 6, tzinfo=dt.timezone.utc)
CODE = {"git_commit": "abcdef1234567890", "git_dirty": False}
CHINOOK = {"datasource_id": "chinook", "engine": "sqlite", "schema_fingerprint": "ceed80fefde051bd",
           "tables": 11, "columns": 64}
SUITE = {"name": "chinook", "path": str(CHINOOK_SUITE_PATH), "digest": load_suite(CHINOOK_SUITE_PATH).digest()}


def _rec(qid, status, pass_no=1, cost=0.02, faithful=None):
    """A case record: ``status`` is the rows' verdict; faithfulness follows it unless ``faithful`` says otherwise."""
    faithful = status == "pass" if faithful is None else faithful
    scores = {"result_match": {"passed": status == "pass", "value": None, "detail": {}},
              "outcome": {"passed": True, "value": None, "detail": {}},
              "faithfulness": {"passed": faithful, "value": None, "detail": {}}}
    overall, reason = report.case_status(scores)
    return {"id": qid, "question": "?", "role": "admin", "expected": "allowed", "status": overall,
            "reason": reason, "lenient_status": overall, "scores": scores,
            "sql": "SELECT 1", "rows": 1, "gold_rows": 1, "pass": pass_no, "tags": ["join"], "difficulty": "easy",
            "cost": cost, "latency_s": 1.5, "rows_digest": "d", "error_codes": [], "refused_unanswerable": False,
            "retries": 0, "timings": {}, "source": "generated", "generated": None,
            "faithfulness": {"faithful": faithful, "unsupported_numbers": [] if faithful else ["9"],
                             "unsupported_entities": [], "checked": 1},
            "tokens_by_node": {"ast_planner": {"calls": 1, "input_tokens": 1000, "cached_input_tokens": 400,
                                               "cache_write_input_tokens": 0, "output_tokens": 100,
                                               "reasoning_tokens": 20}}}


def _board(names=("gpt-5.4",), passes=1, stopped=None, statuses=("pass", "fail"), database=CHINOOK,
           roles=("admin",), cost=0.02, curated=False, suite=SUITE, faithful=None):
    """A mode full ``EvalReport`` with one board per name."""
    configs = {}
    for name in names:
        runs = [_rec(f"q{i}", s, p, cost, faithful) for p in range(1, passes + 1) for i, s in enumerate(statuses)]
        board = report.score_config(runs, passes=passes)
        board.update(models={"astplanner": "openai:gpt-5.4", "decomposer": "openai:gpt-5.4-mini"},
                     planned_cases=len(runs), completed_cases=len(runs))
        configs[name] = board
    return EvalReport(mode="full", suites=[suite], roles=list(roles) if roles else None, passes=passes,
                      curated=curated, max_cost_usd=5.0, spent=0.1, stopped=stopped,
                      prices_checked_on="2026-09-21", configs=configs, database=database)


def _retrieval_report(table=0.9, column=0.7, database=CHINOOK):
    """A mode retrieval ``EvalReport``: three cases, the recall means as given."""
    runs = []
    for i, (t, c) in enumerate(((table, column), (table, column), (table, column))):
        detail = {"table_recall": t, "column_recall": c, "tables_sent": 8.0, "columns_sent": 24.0}
        scores = {"retrieval_recall": {"passed": t == 1.0 and c == 1.0, "value": t, "detail": detail}}
        status, _ = report.case_status(scores)
        runs.append({"id": f"q{i}", "role": "admin", "pass": 1, "expected": "allowed", "status": status,
                     "lenient_status": status, "scores": scores, "cost": 0.0, "latency_s": 0.1})
    board = report.score_config(runs, passes=1)
    board.update(planned_cases=3, completed_cases=3)
    return EvalReport(mode="retrieval", suites=[SUITE], configs={"retrieval": board}, database=database,
                      settings={"retrieval": {"table_k": 8, "planning_k": 12, "fetch_multiplier": 4,
                                              "lambda_mult": 0.7, "full_snapshot_max_tables": 0,
                                              "query": "question"}})


def test_a_record_holds_when_what_on_which_code_data_and_database_and_the_headline_metrics():
    record = records.make_record(_board(passes=2), "gpt-5.4", recorded_at=WHEN, engine_version="0.9.0",
                                 code=CODE, note="slim prompts")
    assert record["schema"] == 2 and record["kind"] == "full"
    assert record["recorded_at"] == "2026-09-21T14:05:06Z"
    assert record["engine_version"] == "0.9.0"
    assert (record["git_commit"], record["git_dirty"]) == ("abcdef1234567890", False)
    assert record["note"] == "slim prompts"
    assert record["database"] == CHINOOK
    # The suite, by its name and digest: two runs compare only on the same digest.
    assert record["dataset"] == {"name": "chinook", "sha256": SUITE["digest"]}
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


def test_the_headline_accuracy_is_execution_accuracy_the_measure_0_6_published():
    # Every answer is unfaithful: overall is 0, but the rows are right in one case of two.
    record = records.make_record(_board(faithful=False), "gpt-5.4", recorded_at=WHEN, engine_version="1",
                                 code=CODE)
    m = record["metrics"]
    assert record["scoreboard"]["accuracy"]["overall"] == 0.0
    assert m["accuracy"] == 0.5 == record["scoreboard"]["accuracy"]["execution"]
    assert m["accuracy_interval"] == record["scoreboard"]["accuracy"]["execution_interval"]
    assert m["accuracy_all_scorers"] == 0.0 and m["accuracy_generated"] == 0.0
    assert "execution" in records.README_FULL_INTRO and "execution" in records.render_history([record])


def test_without_a_known_code_or_database_the_record_says_unknown_not_null():
    board = _board(database=None)
    record = records.make_record(board, "gpt-5.4", recorded_at=WHEN, engine_version="1", code={})
    assert record["git_commit"] == "unknown" and record["git_dirty"] == "unknown"
    assert record["database"]["datasource_id"] == "unknown" and record["note"] is None


def test_several_suites_are_one_dataset_with_a_combined_digest():
    two = _board().model_copy(update={"suites": [SUITE, {"name": "routing", "path": "", "digest": "b" * 64}]})
    dataset = records.make_record(two, "gpt-5.4", recorded_at=WHEN, engine_version="1", code=CODE)["dataset"]
    assert dataset["name"] == "chinook+routing"
    assert dataset["sha256"] not in (SUITE["digest"], "b" * 64) and len(dataset["sha256"]) == 64


def test_a_stopped_run_is_recorded_as_partial():
    record = records.make_record(_board(stopped="max_cost"), "gpt-5.4", recorded_at=WHEN, engine_version="1",
                                 code=CODE)
    assert record["stopped"] == "max_cost" and record["partial"] is True


def test_records_live_under_mode_database_date_sha_config_and_are_never_overwritten(tmp_path):
    board = _board(names=("gpt-5.4", "claude/planner"))
    first = records.write_records(board, tmp_path, recorded_at=WHEN, engine_version="0.9.0", code=CODE)
    content = first[0].read_bytes()
    second = records.write_records(board, tmp_path, recorded_at=WHEN, engine_version="0.9.0", code=CODE, note="x")
    rel = [p.relative_to(tmp_path).as_posix() for p in first + second]
    assert rel == ["full/chinook/2026-09-21_abcdef1_gpt-5.4.json",
                   "full/chinook/2026-09-21_abcdef1_claude-planner.json",
                   "full/chinook/2026-09-21_abcdef1_gpt-5.4-2.json",
                   "full/chinook/2026-09-21_abcdef1_claude-planner-2.json"]
    assert first[0].read_bytes() == content
    assert json.loads(first[0].read_text(encoding="utf-8"))["config"]["name"] == "gpt-5.4"
    [path] = records.write_records(_retrieval_report(), tmp_path, recorded_at=WHEN, engine_version="0.9.0",
                                   code={"git_commit": "unknown", "git_dirty": "unknown"}, note="baseline")
    assert path.relative_to(tmp_path).as_posix() == "retrieval/chinook/2026-09-21_unknown_retrieval.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["database"] == CHINOOK and body["note"] == "baseline"
    assert body["settings"]["table_k"] == 8
    assert body["metrics"] == {"questions": 3, "table_recall": 0.9, "column_recall": 0.7, "perfect_tables": 0,
                               "perfect_columns": 0, "tables_sent": 8.0, "columns_sent": 24.0,
                               "routing_cases": 0, "routing_top1": None, "routing_shortlist": None}


def test_a_mode_plans_run_is_a_record_too(tmp_path):
    plans = _board(names=("plans",), statuses=("pass", "pass")).model_copy(update={"mode": "plans"})
    [path] = records.write_records(plans, tmp_path, recorded_at=WHEN, engine_version="1", code=CODE)
    assert path.relative_to(tmp_path).as_posix() == "plans/chinook/2026-09-21_abcdef1_plans.json"
    page = records.render_history(records.load_records(tmp_path))
    assert "## Mode plans: chinook" in page and "100.0%" in page


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


def test_a_run_stamp_names_when_and_which_code():
    stamp = records.run_stamp(recorded_at=WHEN, engine_version="0.9.0", code=CODE)
    assert stamp == {"recorded_at": "2026-09-21T14:05:06Z", "engine_version": "0.9.0",
                     "git_commit": "abcdef1234567890", "git_dirty": False}


def _snapshot(ds_id, tables, columns):
    """A schema snapshot with ``tables`` tables holding ``columns`` columns between them."""
    per = [columns // tables + (i < columns % tables) for i in range(tables)]
    return SimpleNamespace(contract=SimpleNamespace(
        datasource_id=ds_id, engine_type="sqlite",
        tables={f"t{i}": SimpleNamespace(columns={f"c{j}": object() for j in range(n)}, foreign_keys=[])
                for i, n in enumerate(per)}))


def _demo_ctx():
    """The demo as it stands: Chinook exactly as the committed runs saw it, and the two new databases beside it."""
    snapshots = {"chinook": _snapshot("chinook", CHINOOK["tables"], CHINOOK["columns"]),
                 "support": _snapshot("support", 4, 30), "webanalytics": _snapshot("webanalytics", 5, 34)}
    return SimpleNamespace(ds_registry=SimpleNamespace(list_ids=lambda: sorted(snapshots)),
                           vector_store=SimpleNamespace(snapshot=snapshots.get))


def _fingerprinted_per_datasource(monkeypatch):
    """Fingerprint each contract by its datasource, Chinook's being the one the committed runs recorded."""
    import hashlib

    import nl2sql.schema.fingerprint as protocol
    digests = {"chinook": CHINOOK["schema_fingerprint"].ljust(64, "0")}
    monkeypatch.setattr(protocol, "generate_schema_fingerprint",
                        lambda c: digests.get(c.datasource_id, hashlib.sha256(c.datasource_id.encode()).hexdigest()))


def test_database_identity_fingerprints_the_live_schema_snapshot(monkeypatch):
    import nl2sql.schema.fingerprint as protocol
    ctx = SimpleNamespace(ds_registry=SimpleNamespace(list_ids=lambda: ["chinook"]),
                          vector_store=SimpleNamespace(snapshot={
                              "chinook": _snapshot("chinook", 2, 3)}.get))
    monkeypatch.setattr(protocol, "generate_schema_fingerprint", lambda c: "f" * 64)
    assert records.database_identity(ctx) == {"datasource_id": "chinook", "engine": "sqlite",
                                              "schema_fingerprint": "f" * 16, "tables": 2, "columns": 3}
    empty = SimpleNamespace(ds_registry=SimpleNamespace(list_ids=lambda: ["x"]),
                            vector_store=SimpleNamespace(snapshot=lambda _id: None))
    assert records.database_identity(empty)["schema_fingerprint"] == "unknown"


def test_the_identity_is_the_suites_own_datasources_not_every_registered_one(monkeypatch):
    """With the demo's three databases registered, a Chinook suite run is still `chinook`."""
    _fingerprinted_per_datasource(monkeypatch)
    ctx = _demo_ctx()
    identity = records.database_identity(ctx, _suite_datasources())
    # Byte for byte what the committed 0.6 runs recorded.
    assert identity == CHINOOK
    for path in sorted((REPO / "benchmarks").glob("*/chinook/*.json")):
        committed = json.loads(path.read_text(encoding="utf-8"))
        if committed["recorded_at"] >= "2026-10":
            continue  # 0.7 records: the demo's schema as it is now
        assert committed["database"] == identity, path
        fresh = {**committed, "database": identity}
        assert records.series_key(fresh) == records.series_key(committed)
        assert records.record_relpath(fresh).parent.as_posix() == f"{committed['kind']}/chinook"
    # Left to itself it still describes everything registered, which is what a
    # run really spanning three databases would want.
    assert records.database_identity(ctx)["datasource_id"] == "chinook+support+webanalytics"


def _suite_datasources():
    return sorted({d for c in load_suite(CHINOOK_SUITE_PATH).cases for d in c.datasources})


def test_the_chinook_suite_names_one_datasource():
    assert _suite_datasources() == ["chinook"]


def _write(root, day, name="gpt-5.4", statuses=("pass", "fail"), database=CHINOOK, roles=("admin",), cost=0.02,
           note=None, suite=SUITE):
    return records.write_records(_board(names=(name,), statuses=statuses, database=database, roles=roles, cost=cost,
                                        suite=suite),
                                 root, recorded_at=WHEN.replace(day=day), engine_version="0.9.0", code=CODE,
                                 note=note)


def test_runs_are_comparable_on_mode_suite_schema_and_roles():
    a = records.make_record(_board(), "gpt-5.4", recorded_at=WHEN, engine_version="1", code=CODE)
    assert records.comparable(a, dict(a))
    other_schema = {**a, "database": {**CHINOOK, "schema_fingerprint": "0" * 16}}
    other_data = {**a, "dataset": {"name": "x", "sha256": "1" * 64}}
    other_roles = {**a, "roles": ["sales"]}
    retrieval = {**a, "kind": "retrieval"}
    assert not any(records.comparable(a, b) for b in (other_schema, other_data, other_roles, retrieval))


def test_a_0_6_tier_2_record_is_a_mode_full_run_on_another_suite():
    old = json.loads((REPO / "benchmarks" / "tier2" / "chinook" / "2026-09-23_de42c40_gpt-5.4.json")
                     .read_text(encoding="utf-8"))
    new = records.make_record(_board(), "gpt-5.4", recorded_at=WHEN.replace(month=10), engine_version="0.7.0",
                              code=CODE)
    assert records.mode_of(old) == "full" and records.line_key(old) == records.line_key(new)
    assert records.describe_change(new, old) == "new series (suite changed)"
    # Its headline accuracy was the execution match, which is what a 0.7 record's headline is.
    assert old["metrics"]["accuracy"] == pytest.approx(0.7907)


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


def test_a_run_with_curated_answers_on_is_marked_and_starts_its_own_series():
    off = records.make_record(_board(), "gpt-5.4", recorded_at=WHEN, engine_version="1", code=CODE)
    on = records.make_record(_board(curated=True), "gpt-5.4", recorded_at=WHEN, engine_version="1", code=CODE)
    assert off["curated"] is False and on["curated"] is True
    assert not records.comparable(on, off)
    assert records.describe_change(on, off) == "new series (curated answers changed)"
    # A record written before the field existed ran without the switch: the same series as "off".
    old = {k: v for k, v in off.items() if k != "curated"}
    assert records.comparable(old, off)


def test_a_changed_schema_or_suite_starts_a_new_series(tmp_path):
    _write(tmp_path, 19)
    _write(tmp_path, 20, database={**CHINOOK, "schema_fingerprint": "0123456789abcdef"})
    _write(tmp_path, 21, database={**CHINOOK, "schema_fingerprint": "0123456789abcdef"}, roles=("sales",))
    _write(tmp_path, 22, database={**CHINOOK, "schema_fingerprint": "0123456789abcdef"}, roles=("sales",),
           suite={**SUITE, "digest": "c" * 64})
    recs = records.load_records(tmp_path)
    previous = records.previous_runs(recs)
    by_day = {r["recorded_at"][8:10]: r for r in recs}
    assert records.describe_change(by_day["20"], previous[id(by_day["20"])]) == "new series (schema changed)"
    assert records.describe_change(by_day["21"], previous[id(by_day["21"])]) == "new series (roles changed)"
    assert records.describe_change(by_day["22"], previous[id(by_day["22"])]) == "new series (suite changed)"
    page = records.render_history(recs)
    assert page.split("### All runs")[1].count("new series") == 3


def test_history_groups_by_mode_then_database_newest_first(tmp_path):
    _write(tmp_path, 19, statuses=("fail", "fail"))
    _write(tmp_path, 20, note="slim prompts")
    _write(tmp_path, 21, name="mini", statuses=("pass", "pass"))
    _write(tmp_path, 21, database={**CHINOOK, "datasource_id": "northwind", "schema_fingerprint": "9" * 16})
    for day, table in ((19, 0.9), (20, 0.95)):
        records.write_records(_retrieval_report(table=table), tmp_path, recorded_at=WHEN.replace(day=day),
                              engine_version="0.9.0", code=CODE)
    page = records.render_history(records.load_records(tmp_path))
    headings = [line for line in page.splitlines() if line.startswith("## ")]
    assert headings == ["## Mode full: chinook (sqlite, 11 tables / 64 columns)",
                        "## Mode full: northwind (sqlite, 11 tables / 64 columns)",
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
    full_part, retrieval_part = block.split(records.README_RETRIEVAL_HEADING)
    assert records.README_FULL_HEADING in full_part
    assert "| gpt-5.4 | chinook | 2026-09-20 |" in full_part and "| gpt-5.4 | northwind | 2026-09-21 |" in full_part
    assert "| mini | chinook | 2026-09-21 |" in full_part and "table_recall" not in full_part
    assert "| chinook | 2026-09-20 | abcdef1 | 95.0% | 70.0% | - |" in retrieval_part


DEMO = {"datasource_id": "chinook+support+webanalytics", "engine": "sqlite", "schema_fingerprint": "5" * 16,
        "tables": 20, "columns": 121}


def _routing_report(verdicts=((True, True), (False, True))):
    """A mode retrieval ``EvalReport`` on a routing suite: routing scored, no recall (the cases name no tables)."""
    runs = []
    for i, (top1, shortlist) in enumerate(verdicts):
        scores = {"routing": {"passed": top1 and shortlist, "value": None,
                              "detail": {"top1": top1, "shortlist": shortlist}}}
        status, _ = report.case_status(scores)
        runs.append({"id": f"r{i}", "role": "admin", "pass": 1, "expected": "allowed", "status": status,
                     "lenient_status": status, "scores": scores, "cost": 0.0, "latency_s": 0.1})
    board = report.score_config(runs, passes=1)
    board.update(planned_cases=len(runs), completed_cases=len(runs))
    suite = {"name": "routing", "path": "routing.yaml", "digest": "7" * 64}
    return EvalReport(mode="retrieval", suites=[suite], configs={"retrieval": board}, database=DEMO,
                      settings={"retrieval": {"table_k": 8, "planning_k": 12, "fetch_multiplier": 4,
                                              "lambda_mult": 0.7, "full_snapshot_max_tables": 0,
                                              "query": "question"}})


def test_a_routing_record_holds_routing_top1_and_shortlist_and_renders_without_recall(tmp_path):
    record = records.make_record(_routing_report(), "retrieval", recorded_at=WHEN, engine_version="1", code=CODE)
    m = record["metrics"]
    assert (m["routing_cases"], m["routing_top1"], m["routing_shortlist"]) == (2, 0.5, 1.0)
    assert m["questions"] == 0 and m["table_recall"] is None
    records.write_records(_routing_report(), tmp_path, recorded_at=WHEN.replace(day=19), engine_version="1",
                          code=CODE)
    records.write_records(_routing_report(((True, True), (True, True))), tmp_path, recorded_at=WHEN,
                          engine_version="1", code=CODE)
    recs = records.load_records(tmp_path)
    page = records.render_history(recs)
    assert "## Retrieval recall: chinook+support+webanalytics" in page
    assert "| 100.0% / 100.0% |" in page and "| 50.0% / 100.0% |" in page
    assert "top-1 +50.0 pp, shortlist +0.0 pp" in page
    block = records.render_readme_block(recs)
    assert "| chinook+support+webanalytics | 2026-09-21 | abcdef1 | - | - | 100.0% / 100.0% |" in block


def test_the_pages_name_nl2sql_eval_not_nl2sql_benchmark(tmp_path):
    _write(tmp_path, 19)
    page = records.render_history(records.load_records(tmp_path)) + records.render_readme_block([])
    assert "nl2sql eval publish" in page and "nl2sql eval run" in page
    assert "nl2sql benchmark" not in page


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
    assert records.FULL_EMPTY in block and records.RETRIEVAL_EMPTY in block
    assert block.index(records.README_FULL_HEADING) < block.index(records.README_RETRIEVAL_HEADING)
    assert records.load_records(tmp_path / "missing") == []


def test_the_readme_block_says_when_one_mode_has_no_run_yet(tmp_path):
    records.write_records(_retrieval_report(), tmp_path, recorded_at=WHEN, engine_version="0.9.0", code=CODE)
    block = records.render_readme_block(records.load_records(tmp_path))
    assert records.FULL_EMPTY in block and records.RETRIEVAL_EMPTY not in block
    assert "| chinook | 2026-09-21 | abcdef1 | 90.0% | 70.0% | - | first run |" in block


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
    (repo / "full" / "chinook").mkdir(parents=True)
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


def test_the_ci_baselines_are_on_the_suites_as_they_are_now():
    """CI's key-free gate compares against these: a changed suite needs a new baseline (`nl2sql eval publish`)."""
    committed = records.load_records(REPO / records.BENCHMARKS_DIR)
    routing = {"digest": load_suite(ROUTING_SUITE_PATH).digest(), "database": "chinook+support+webanalytics"}
    baselines = {"plans": (SUITE["digest"], "chinook"), "retrieval": (SUITE["digest"], "chinook"),
                 "routing": (routing["digest"], routing["database"])}
    newest = {}
    for name, (digest, database) in baselines.items():
        mode = "retrieval" if name == "routing" else name
        newest[name] = next(r for r in committed
                            if records.mode_of(r) == mode and r["database"]["datasource_id"] == database)
        assert newest[name]["dataset"]["sha256"] == digest, name
        # The recording rule (benchmarks/README.md): CI's baselines come from CI's own runner.
        assert "ubuntu-latest" in newest[name]["note"] and "actions/runs/" in newest[name]["note"], name
    assert newest["plans"]["metrics"]["accuracy"] == 1.0
    assert newest["retrieval"]["settings"]["embedding"] and newest["routing"]["settings"]["embedding"]
    assert newest["routing"]["metrics"]["routing_cases"] == len(load_suite(ROUTING_SUITE_PATH).cases)


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
    assert history == records.render_history(committed), "run `nl2sql eval publish` from the repo root and commit the result"
    assert readme == records.replace_block(readme, records.render_readme_block(committed)), \
        "run `nl2sql eval publish` from the repo root and commit the result"


# --- statistics in the record and in the history -----------------------------


def test_a_record_keeps_the_interval_for_both_scores_and_pass_k():
    record = records.make_record(_board(passes=2), "gpt-5.4", recorded_at=WHEN, engine_version="1", code=CODE)
    m = record["metrics"]
    assert m["accuracy_interval"] == [round(b, 4) for b in report.wilson_interval(2, 4)]
    assert m["lenient_accuracy_interval"] == m["accuracy_interval"]
    # Both passes score the same here, so half the questions pass every pass.
    assert m["pass_k"] == {"k": 2, "questions": 2, "strict": 0.5, "lenient": 0.5}
    assert records.make_record(_board(), "gpt-5.4", recorded_at=WHEN, engine_version="1",
                               code=CODE)["metrics"]["pass_k"] is None


def test_the_history_delta_names_the_questions_that_flipped_and_the_p_value(tmp_path):
    _write(tmp_path, 19, statuses=("fail", "fail", "fail", "pass"))
    _write(tmp_path, 20, statuses=("pass", "pass", "pass", "pass"))
    recs = records.load_records(tmp_path)
    newest = recs[0]
    old = records.previous_runs(recs)[id(newest)]

    assert records.flips(newest, old) == {"questions": 4, "pass_to_fail": [],
                                          "fail_to_pass": ["q0/admin", "q1/admin", "q2/admin"],
                                          "p_value": 0.25}
    change = records.describe_change(newest, old, show_flips=True)
    assert "0 flipped to fail, 3 to pass (McNemar p=0.250)" in change
    # The README block keeps the headline deltas only.
    assert "flipped" not in records.describe_change(newest, old)


def test_the_flips_read_execution_accuracy_too(tmp_path):
    # Faithfulness alone changes: the rows are right both times, so nothing flipped.
    [a] = records.write_records(_board(statuses=("pass",), faithful=False), tmp_path,
                                recorded_at=WHEN, engine_version="1", code=CODE)
    [b] = records.write_records(_board(statuses=("pass",)), tmp_path,
                                recorded_at=WHEN.replace(day=22), engine_version="1", code=CODE)
    old, new = (json.loads(p.read_text(encoding="utf-8")) for p in (a, b))
    assert records.flips(new, old)["fail_to_pass"] == []


def test_a_record_without_per_question_results_has_no_flips(tmp_path):
    _write(tmp_path, 19)
    _write(tmp_path, 20)
    recs = records.load_records(tmp_path)
    stripped = {**recs[0], "scoreboard": {}}
    assert records.flips(stripped, recs[1]) is None


def test_a_record_keeps_only_what_the_gate_and_the_pages_read(tmp_path):
    """The full report stays in eval_<mode>.json (a CI artifact); a committed record is slim and compact."""
    full = _board(passes=2)
    [path] = records.write_records(full, tmp_path, recorded_at=WHEN, engine_version="1", code=CODE)
    text = path.read_text(encoding="utf-8")
    assert text.count("\n") == 1 and '": ' not in text  # one compact line
    record = json.loads(text)
    for case in record["scoreboard"]["results"]:
        assert set(case) <= {"id", "role", "pass", "status", "lenient_status", "scores", "generated", "cost"}
        for score in case["scores"].values():
            assert set(score) <= {"passed", "value", "detail"}
            assert set(score.get("detail", {})) <= {"lenient", "error"}
    # Built from the full board, so the headline is unchanged, and the slim cases still gate.
    assert record["metrics"] == records.make_record(full, "gpt-5.4", recorded_at=WHEN, engine_version="1",
                                                    code=CODE)["metrics"]
    from nl2sql.evaluation import gate
    assert gate.gate(full, record).ok
